# Changelog

## Unreleased

### Upgrade notes (action needed)

- **API endpoints move off kea-ctrl-agent on Kea 2.7.2 and newer.** ISC deprecated kea-ctrl-agent, and with the default `kea_api_mode: auto` each daemon now serves the REST API itself:

  | Daemon | New port |
  |---|---|
  | `kea-dhcp4` | `:8004` |
  | `kea-dhcp6` | `:8006` |
  | `kea-dhcp-ddns` | `:8053` |

  This applies to ISC's 3.x packages and AppStream on EL10. kea-ctrl-agent (`:8000`) is stopped and disabled. Point API clients at the new ports. Requests may keep their `"service"` parameter, which the daemons ignore. In the phpIPAM role, set `phpipam_kea_api_urls`. Set `kea_api_mode: ctrl_agent` to keep the old endpoint for now. EPEL's Kea on EL8/EL9 stays on kea-ctrl-agent.
- **`kea_dhcp4_interfaces` and `kea_dhcp6_interfaces` default to `[]`** instead of `["*"]`. The role fails until a family that has subnets lists its interfaces. Set `["*"]` to keep the old behaviour.
- **A non-loopback `kea_api_host` needs `kea_api_users`.** Otherwise the role fails, unless `kea_api_allow_unauthenticated: true`.
- **HA listener ports accept only the HA peers.** firewalld now uses rich rules (`kea_firewalld_ha_restrict_to_peers`) instead of opening the ports to the zone. The role also closes Kea ports it no longer uses, such as `:8000` after the API move (`kea_firewalld_remove_stale`), unless another service listens on them.
- **SELinux ports are no longer labelled on stock RHEL.** RHEL's targeted policy has no Kea module, and Kea runs unconfined, so labels are not needed. Earlier releases labelled the API and HA ports `http_port_t`, changing 8000 (`soundd_port_t`) and 8002 (`xen_port_t`) on the way. Remove those local changes if you like, which also restores the policy's own types:
  ```bash
  semanage port -l -C            # list local port changes
  semanage port -d -p tcp 8000   # repeat for each Kea port listed
  ```
  `kea_selinux_permissive_domain` now fails unless a Kea policy module is loaded.
- **phpIPAM sync deletion guards.**
  - A phpIPAM subnet that does not exist is an error.
  - An empty answer for a subnet no longer removes every reservation in it, unless `kea_phpipam_allow_empty_source: true`.
  - Kea's certificate is checked with `kea_phpipam_api_verify_tls` and `kea_phpipam_api_ca_file`, no longer with phpIPAM's `kea_phpipam_verify_tls`.
- **Configuration changes reload Kea instead of restarting it**, so HA no longer resyncs after every change. Package, drop-in, SELinux and certificate changes still restart. Run HA clusters with `serial: 1` (see the README).

### Renamed variables (the old names still work)

| Old | New |
|---|---|
| `kea_ctrl_agent_enabled` | `kea_api_enabled` |
| `kea_ctrl_agent_host` | `kea_api_host` |
| `kea_ctrl_agent_tls_*` | `kea_api_tls_*` |
| `kea_phpipam_api_url` | `kea_phpipam_api_urls` (one URL per service) |

### Added

- `kea_api_mode` (`auto`, `daemon`, `ctrl_agent`) and the per-daemon API ports `kea_api_dhcp4_port`, `kea_api_dhcp6_port`, `kea_api_ddns_port`.
- HTTPS between HA peers, with client certificates required by default (`kea_ha_tls_*`).
- A port check before anything is configured: the role fails if another service already listens on a port Kea needs, or if two Kea ports collide (`kea_port_check`).
- `kea_db_init` creates or upgrades every MySQL / PostgreSQL database Kea uses (the lease and hosts databases of both families), not only `kea_hosts_database`. It installs the database client that `kea-admin` needs.
- phpIPAM sync options:
  - a private CA and client certificates for the Kea API (`kea_phpipam_api_ca_file`, `_cert_file`, `_key_file`)
  - a private CA for phpIPAM (`kea_phpipam_ca_file`)
  - `kea_phpipam_max_deletes`
- Documentation:
  - NTP servers (DHCPv4 option 42; DHCPv6 options 31 and 56)
  - default gateway, DNS servers, domain name and search list, for both families
  - Firewall and Operations sections (rolling upgrades, health checks, backups, troubleshooting)
- Test scenarios:
  - `ha-lb` and `ha-backup` (the other HA modes)
  - `migrate` (kea-ctrl-agent to the daemon API on a live host)
  - `integration` (MariaDB, API over HTTPS, phpIPAM sync with a mock phpIPAM)
  - `distro` (AppStream Kea on EL10)

  The `ha` scenario now runs HA over HTTPS behind firewalld.

### Fixed

- `kea_install_source: distro` failed on EL10: AppStream's Kea 3.0 rejects control sockets under `/run/kea`. The role now uses `/var/run/kea` for every install source, which EPEL's Kea on EL8/EL9 also accepts.
- Template tasks no longer print database passwords and TSIG secrets under `--diff`.
- Re-runs no longer contact ISC's Cloudsmith repository to check the repository file, so a slow or unreachable CDN no longer fails them.
- HA without a dedicated listener used `http://` peer URLs even when kea-ctrl-agent served HTTPS.
- The phpIPAM sync could not reach a Kea API that requires client certificates, or one whose certificate comes from a private CA.
- The README wrongly said that no passwords appear in the Kea configuration, and that AppStream's Kea on EL10 lacks `host_cmds` and GSS-TSIG.
