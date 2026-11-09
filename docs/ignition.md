# Ignition: OPC UA server and tag JSON (PLAN 2.4)

OpenFactory can serve a scene's I/O table over OPC UA and export Ignition 8.3 tags that bind to
it, so Ignition can be the HMI on a simulated plant (for example, with OpenPLC controlling it
over Modbus). OpenFactory is a training and demo tool, not a safety-rated simulation; anything
built from these files for a client needs HIS Controls PE review.

## Serve OPC UA

```bash
uv sync --extra opcua            # asyncua, LGPL-3.0, an optional extra (THIRD_PARTY.md)
uv run openfactory run scenes/sorting-by-height.yaml --opcua
```

`--opcua` serves OPC UA next to the scene's own PLC protocol (Modbus for the shipped scenes);
a scene with `plc: {protocol: opcua}` serves OPC UA as its PLC server. The endpoint is
`opc.tcp://127.0.0.1:4840/openfactory/` (`[opcua]` in `openfactory.toml`, `--opcua-port`,
`--bind` to open it beyond loopback, PLAN §6.4). Anonymous, no security policy: keep it off
plant networks.

Address space: namespace `urn:openfactory:sim`; under `Objects`, a folder named after the scene,
a folder per component, and a variable per tag. Each variable's NodeId is its tag name as a
string (`s=CONV_01_RUN_DO`), so clients find the same nodes after a restart. DI and AI variables
are read-only; a client's write to a DO or AO reaches the simulation like a PLC's.
`SIM_HEARTBEAT_DI` (PLAN §6.1) sits in the scene folder.

In the gateway, add an OPC UA connection to that endpoint (Config → OPC Connections → Servers),
named `OpenFactory` to match the default `--opc-server` below.

## Export the tags

```bash
uv run openfactory export scenes/sorting-by-height.yaml --format ignition -o sorter-tags.json
```

Options: `--area NAME` (default: the scene name in capitals), `--opc-server NAME` (default
`OpenFactory`), or `--device NAME` to address a Logix controller through Ignition's driver
instead (`ns=1;s=[NAME]TAG`, for tags made from the `--format l5x` stubs; the device alias from
the gateway, never an address). Import into the provider in the Designer's Tag Browser.

## Structure

The file follows the HIS Controls Ignition conventions (folder per area, UDT instance per
component). A test (`tests/unit/test_ignition_tags.py`) holds every shipped scene's export to
these rules:

```text
{"version": "8.3.0", "tags": [
  {"name": "_types_", "tagType": "Folder", "tags": [UdtType, ...]},    first: types before instances
  {"name": AREA, "tagType": "Folder", "tags": [UdtInstance, ..., AtomicTag SIM_HEARTBEAT]}
]}
```

- **UdtType** `OF_<ComponentType>` (`OF_BeltConveyor`), one per component type in the scene.
  Parameters `InstanceName` and `Tag_<Member>` (strings). Members are `AtomicTag`s, one per port
  any instance of the type binds, named by the port in PascalCase (`run` → `Run`):
  `valueSource: "opc"`, `opcServer`, `opcItemPath: "nsu=urn:openfactory:sim;s={Tag_<Member>}"`,
  `dataType` `Boolean` or `Float4`, `accessRights` `Read_Write` for DO/AO and `Read_Only` for
  DI/AI, `historyEnabled` only on AI members (process values), and the port description as
  `documentation`.
- **UdtInstance**, one per component that binds I/O, named by its id (`CONV_01`), with `typeId`,
  `InstanceName`, every `Tag_<Member>` set to the scene tag, and the component description. A
  member the instance does not bind is overridden to `enabled: false`; an analog member gets its
  instance's `engUnit`, `engLow`, `engHigh`. Components without I/O (part removers) have none.
- **SIM_HEARTBEAT**, an `AtomicTag` in the area folder, read-only.

No alarms are generated: a scene has no rationalized alarm setpoints (ISA-18.2).

Not yet verified against a running gateway: the import itself, Good quality on every tag, and a
Perspective view writing a run bit (PLAN 2.4, "Not done").

## Sparkplug B to MQTT Engine (PLAN 3.5)

`run --sparkplug` publishes the scene's I/O table as one Sparkplug B 3.0 edge node to an MQTT
broker, for Ignition's MQTT Engine module (Cirrus Link) or any Sparkplug host:

```bash
uv run openfactory run scenes/tank-level-pid.yaml --script examples/pyplc/tank-level-pid.py --sparkplug
```

The broker is `[sparkplug]` in `openfactory.toml` (`broker`, `port`, `group`; default
`127.0.0.1:1883`, group `OpenFactory`); the edge node id is the scene's name unless `node` sets
one. A broker on another machine is a connection: put it in `openfactory.local.toml`. Nothing to
install: the MQTT client and the payload encoding are part of OpenFactory
(`src/openfactory/protocols/sparkplug.py`).

What it sends (Sparkplug 3.0 chapters 4 to 6):

- CONNECT with an NDEATH Will (QoS 1, not retained) carrying `bdSeq`, which counts connections;
  SUBSCRIBE to `spBv1.0/<group>/NCMD/<node>`; then NBIRTH with every tag as a metric (name,
  alias, datatype: Boolean for a BOOL tag, Float for a float32 tag), `bdSeq` and
  `Node Control/Rebirth`.
- NDATA twice a second with the metrics that changed, by alias.
- A rebirth when the host asks (writing `Node Control/Rebirth` true). Any other NCMD is ignored
  and logged: the node is **read-only telemetry**. The PLC controls the plant over Modbus TCP or
  OPC UA; a host cannot write a DO or AO through Sparkplug.
- NDEATH on a clean stop; the broker sends the Will if the run dies.

A broker that is down does not stop the run: the node retries every 2 s and is born when the
broker comes up.

### Test with Ignition (deferred: Docker is down)

The automated tests run the node against a small MQTT 3.1.1 broker written for the tests
(`tests/integration/mqtt_test_broker.py`), and in CI against Mosquitto with paho-mqtt as an
independent client (`tests/integration/test_sparkplug_mosquitto.py`). They have not run it
against MQTT Engine. The procedure, for the first run with
Docker up:

1. Start a broker on the host, for example Mosquitto 2.x with a listener on 1883
   (`listener 1883` and `allow_anonymous true` in a lab-only config).
2. In the Ignition 8.3 container (the existing `ign-sim`), install the MQTT Engine module and
   start its trial (the 2-hour trial resets from the gateway). Under MQTT Engine → Settings →
   Servers, add the broker by the host's address as the container sees it. Leave the primary host
   settings at their defaults; the node does not wait for a primary host.
3. Run the scene with `--sparkplug` as above.
4. Check, and note each result in PLAN 3.5:
   - the edge node appears under the MQTT Engine tag provider,
     `[MQTT Engine]Edge Nodes/OpenFactory/tank-level-pid/...`
     `[TBD: verify the path on the first Ignition 8.3 run]`, with every tag of the scene;
   - `TK_01_LT_AI` updates twice a second and its quality is Good;
   - writing the node's `Node Control/Rebirth` tag produces a new NBIRTH (the run's log shows no
     warning; the Engine's Node Info shows a new birth);
   - stopping the run with Ctrl+C marks the node offline at once (NDEATH); killing it (`kill
     -9`) marks it offline when the broker's keep-alive expires (the Will);
   - writing any other node tag from Ignition changes nothing in the simulation (read-only).
