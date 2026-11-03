# Lance terraform sur l'infra avec les secrets du magasin global (setup.py) :  .\tf.ps1 plan   /   .\tf.ps1 apply
python "$PSScriptRoot\..\setup.py" tf infra @args
