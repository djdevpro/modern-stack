# Private administration

SSH (22) and Dokploy (3000) have no public firewall allowance, for IPv4 or IPv6.
Connect NetBird before opening `http://<server-name>.netbird.cloud:3000`.
Dokploy authentication still applies.

Run `python setup.py apply infra` from a machine connected to NetBird with the
administrator SSH key and a verified server entry in OpenSSH known_hosts.
Terraform reconciles the Dokploy Traefik route on every apply using
`configure-private-admin.py`. Its IP allowlist is derived from the server's wt0
networks and does not trust a client-supplied X-Forwarded-For header.
The original router is backed up under `/opt/backups/private-admin`.
Unexpected router layouts fail explicitly rather than overwriting custom settings.

The replacement shown for terraform_data.private_admin only reruns configuration;
it does not destroy the server or its applications. The legacy admin_ips variable
is retained for compatibility and grants no public administration access.
Keep the provider console available for VPN recovery. Recheck this policy after
Dokploy upgrades or changes to its router configuration.
