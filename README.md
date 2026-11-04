# modern-stack

A self-hosted platform on a single small server: mail, single sign-on, monitoring and your own apps, deployed and kept up to date automatically.

## The idea

- **Terraform** creates the server (Hetzner) with its firewall; private admin access goes through a NetBird VPN.
- **Docker Compose** runs the core services: Stalwart (mail), Keycloak (single sign-on), Prometheus and Grafana (monitoring), behind Traefik with automatic HTTPS. Dokploy deploys the other apps.
- **Git is the source of truth.** A small agent on the server pulls `main`, waits for a green CI, applies the change and checks the containers are healthy. Nothing can push into the server.
- **Security updates are automatic.** Images are pinned by digest. Every night Trivy scans them for CVEs, Dependabot opens the fix, and safe patches are merged and deployed without anyone touching the server.

## Layout

| Folder | Content |
|---|---|
| `terraform/` | Server, firewall, reverse DNS |
| `stack/` | Core services (Docker Compose) and helper scripts |
| `deploiement/` | Pull-based deployment agent |
| `.github/` | CI, deployment status, nightly CVE scan, Dependabot |

No secrets live in this repository.
