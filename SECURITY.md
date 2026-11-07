# Security Policy

OpenFactory is a training and demonstration tool. It is not a safety-rated simulator, and its
servers are lab tools: keep them off plant and business networks.

## Reporting a vulnerability

Please report security problems privately, not in a public issue or pull request.

- **Email:** [Timothy.hunt.his@outlook.com](mailto:Timothy.hunt.his@outlook.com), with
  "OpenFactory security" in the subject.
- **GitHub:** if the repository's *Security* tab offers *Report a vulnerability*, you may use that
  form instead. It opens a private advisory that only the maintainers can see.

Include what you can of the following:

- the OpenFactory version (the first line `openfactory run` logs, or the commit) and how you run it (CLI flags,
  `openfactory.toml` settings that matter, operating system, browser for the viewer);
- the component involved (Modbus or OPC UA server, the web server and its API, the viewer, scene
  or bundle loading, an exporter);
- steps or a minimal scene that reproduce it, and what an attacker gains.

We will acknowledge a report, keep you informed while we work on a fix, and credit you in the
changelog and the advisory unless you ask us not to. Please give us a reasonable time to release a
fix before you disclose the problem publicly.

## Supported versions

OpenFactory has not made a tagged release yet. Until 1.0.0, fixes go into the latest release and
the `main` branch only.

| Version | Supported |
|---------|-----------|
| `main` and the latest 0.x release | Yes |
| Older 0.x releases | No |

## Security model

These are the defaults a report is measured against (PLAN §6.4):

- **Every server binds to loopback** (`127.0.0.1`) unless the operator passes `--bind` or writes a
  `bind` in their own `openfactory.toml` or `openfactory.local.toml`. A scene file cannot open the
  network: its `plc.bind` override is restricted to loopback and fails to load otherwise.
- **Modbus TCP has no authentication.** The protocol has none. That is by design, not a
  vulnerability, and it is why the servers stay on loopback.
- **The OPC UA server is anonymous by default**, with no security policy. Treat it like Modbus.
- **The fault-injection API** (`/api/fault`) changes what a PLC sees. On loopback it is open.
  Bound beyond loopback, every request needs the configured fault token in the
  `X-OpenFactory-Fault-Token` header; with no token configured the API is off there.
- **Scenes, bundles (`.ofz`) and recordings are data**, but they come from other people. Loading
  one must never run code or write outside the places the documentation names. A Python stand-in
  PLC (`--script`) is code and runs with your permissions. Only run scripts you trust, including
  a script a bundle carries (`--bundled-script`).

### In scope

- Anything that lets a scene, bundle, recording, mesh, or library composite run code, read or
  write files outside where it should, or exhaust the host when loaded.
- A server reachable beyond loopback without the operator opening it.
- The fault API accepting a request beyond loopback without the right token.
- Cross-site scripting or request forgery against the viewer or the web API.

### Out of scope

- The absence of authentication on Modbus TCP, and anonymous OPC UA, on a server the operator
  bound beyond loopback.
- Anything that needs a `--script` file the attacker wrote.
- Use of OpenFactory to validate safety functions. It is not safety-rated and must never be used
  that way.
