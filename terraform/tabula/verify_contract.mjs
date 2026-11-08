// Validate a disposable import against Tabula's actual shared schemas and query engine.
// Run with the tsx installation belonging to the Tabula source checkout.
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const [source, fixture] = process.argv.slice(2);
if (!source || !fixture) throw new Error('Usage: verify_contract.mjs TABULA_SOURCE FIXTURE_JSON');
const load = (path) => import(pathToFileURL(resolve(source, path)).href);
const [fieldsLib, viewLib, queryLib, formulaLib] = await Promise.all([
  load('packages/shared/src/fields/index.ts'),
  load('packages/shared/src/views/index.ts'),
  load('packages/shared/src/query/index.ts'),
  load('packages/shared/src/formula/index.ts'),
]);
const fixtureData = JSON.parse(await readFile(fixture, 'utf8'));
const parsedTables = new Map();
for (const table of fixtureData.tables) {
  const refs = table.fields.map((f) => ({ id: f.id, name: f.title }));
  const fields = table.fields.map((f) => {
    const options = { ...f.options };
    if (f.type === 'Formula') {
      const result = formulaLib.formulaToStorage(options.formula, refs);
      assert.equal(result.ok, true, `${table.title}: invalid formula references`);
      options.formula = result.formula;
    }
    const parsed = fieldsLib.parseField({ id: f.id, name: f.title, type: f.type, options: JSON.stringify(options) });
    assert.ok(parsed, `${table.title}: invalid field ${f.title}`);
    return parsed;
  });
  for (const field of fields.filter((f) => f.type === 'Formula')) {
    const compiled = formulaLib.compileFormula(field.options.formula, fields, field.id);
    assert.equal(compiled.ok, true, `${table.title}: invalid formula ${field.name}`);
  }
  for (const view of table.views) {
    const config = viewLib.parseViewConfig(view.type, JSON.stringify(view.config));
    assert.ok(config, `${table.title}: invalid view ${view.title}`);
    const byId = new Map(fields.map((f) => [f.id, f]));
    if (config.type === 'kanban') assert.equal(byId.get(config.stackFieldId)?.type, 'SingleSelect');
    if (config.type === 'calendar') assert.ok(['Date', 'DateTime'].includes(byId.get(config.dateFieldId)?.type));
    if (viewLib.isQueryable(config)) {
      for (const condition of viewLib.listConditions(config.filters)) {
        const target = byId.get(condition.fieldId);
        assert.ok(target && viewLib.operatorsForField(target).includes(condition.operator), `${table.title}: incompatible view ${view.title}`);
        assert.equal(viewLib.isConditionComplete(condition, target), true, `${table.title}: incomplete filter in ${view.title}`);
      }
      for (const sort of config.sorts) assert.ok(byId.has(sort.fieldId));
    }
  }
  parsedTables.set(table.title, { table, fields });
}

const interactions = parsedTables.get('Interactions');
const resultField = interactions.fields.find((f) => f.name === 'Résultat');
const responseField = interactions.fields.find((f) => f.name === 'Réponse enregistrée');
const refusalField = interactions.fields.find((f) => f.name === 'Refus enregistré');
const choiceId = (field, title) => field.options.choices.find((c) => c.title === title).id;
const format = { locale: 'fr', timeZone: 'Europe/Paris', userName: () => '' };
const evaluate = (field, result) => {
  const compiled = formulaLib.compileFormula(field.options.formula, interactions.fields, field.id);
  assert.equal(compiled.ok, true);
  return compiled.formula.evaluate({ id: 'rec00000000000000', cells: { [resultField.id]: choiceId(resultField, result) } }, { format });
};
assert.equal(evaluate(responseField, 'Réponse'), 1);
assert.equal(evaluate(responseField, 'Rendez-vous fixé'), 1);
assert.equal(evaluate(responseField, 'Sans réponse'), 0);
assert.equal(evaluate(refusalField, 'Refus'), 1);
assert.equal(evaluate(refusalField, 'Réponse'), 0);

const prospect = parsedTables.get('Prospects');
const byName = (name) => prospect.fields.find((f) => f.name === name);
const action = byName('Prochaine action');
const qualification = byName('Qualification');
const ctx = { now: new Date('2026-11-08T12:00:00Z'), timeZone: 'Europe/Paris', userId: '', format };
const testView = (title, changes) => {
  const view = prospect.table.views.find((v) => v.title === title);
  const config = viewLib.parseViewConfig(view.type, JSON.stringify(view.config));
  const cells = {
    [byName('Nom').id]: 'Alice',
    [qualification.id]: choiceId(qualification, 'Qualifié'),
    [action.id]: choiceId(action, 'Relancer'),
    [byName('Date de prochaine action').id]: '2026-11-08T10:00:00Z',
    [byName('Nombre de réponses').id]: 0,
    [byName('Nombre de refus').id]: 0,
  };
  for (const [name, value] of Object.entries(changes)) cells[byName(name).id] = value;
  const record = { id: 'rec00000000000000', cells, autoNumber: 1, createdAt: '', updatedAt: '', createdBy: '', updatedBy: '' };
  const followupField = byName('Relance autorisée (réponses)');
  const compiled = formulaLib.compileFormula(followupField.options.formula, prospect.fields, followupField.id);
  assert.equal(compiled.ok, true);
  cells[followupField.id] = compiled.formula.evaluate(record, { format });
  return queryLib.runViewQuery([record], config, prospect.fields, ctx, {}).records.length;
};
assert.equal(testView('À contacter', {}), 1);
assert.equal(testView('À traiter aujourd’hui', {}), 1);
assert.equal(testView('À traiter aujourd’hui', { 'Nombre de réponses': 1 }), 0);
assert.equal(testView('À traiter aujourd’hui', { 'Nombre de réponses': 1, 'Réponses au début de la séquence': 1 }), 1);
assert.equal(testView('À traiter aujourd’hui', { 'Nombre de réponses': 2, 'Réponses au début de la séquence': 1 }), 0);
assert.equal(testView('À traiter aujourd’hui', { 'Nombre de refus': 1, 'Réponses au début de la séquence': 1 }), 0);
assert.equal(testView('À traiter aujourd’hui', { 'Refus de contact': true }), 0);
assert.equal(testView('À traiter aujourd’hui', { 'Nombre de réponses': 1, 'Prochaine action': choiceId(action, 'Rendez-vous') }), 1);
assert.equal(testView('À traiter aujourd’hui', { 'Date de prochaine action': '2026-11-09T10:00:00Z' }), 0);
console.log('Tabula contract checks passed: fields, formulas, 12 view configurations and follow-up filters.');
