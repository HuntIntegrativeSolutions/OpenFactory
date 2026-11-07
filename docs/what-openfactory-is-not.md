# What OpenFactory is not

OpenFactory simulates a small plant, conveyors, sensors and actuators, so that a real or soft PLC
can control it, for training, prototyping and demonstrations. Read this page before you rely on it
for anything else.

## Not safety-rated

OpenFactory is not a safety-rated simulation, and no program tested against it is thereby
validated. Never use it to validate safety instrumented functions, machine guarding, emergency
stops, interlocks, or any logic whose failure could injure someone or damage equipment. A program
that passes every check in OpenFactory still needs its normal engineering review and site
acceptance testing on the real machine.

## Not virtual commissioning

OpenFactory does not model a specific machine from its CAD data, its drives, or its real cycle
times, and it is not a substitute for virtual commissioning tools that do. Its behaviour is
plausible, not exact:

- parts move by a simple kinematic model in the core (friction-limited belt speed, pushes,
  queues, falls), and the browser's Rapier physics only draws them, agreeing with the core within
  about two ticks (PLAN 1.12);
- sensors, actuators and conveyors are idealised: a pusher strokes in its configured time, a
  photo eye switches on a box edge, a distance sensor reads the exact distance, and nothing wears,
  drifts or bounces unless a fault is injected;
- the simulation runs in soft real time on a desktop operating system, at 50 Hz by default, with
  no hard timing guarantee.

Commissioning a line, sizing a drive, or proving a cycle time needs the real equipment or a tool
built for that job.

## Not a PLC

OpenFactory is the plant, not the controller. It does not emulate PLC firmware, execute ladder or
Structured Text, or reproduce a controller's scan, timing or fault behaviour. A PLC talks to it
over Modbus TCP, and an HMI or SCADA client over OPC UA; an EtherNet/IP client for Logix
controllers is planned (PLAN 2.1, 2.2). A Python stand-in script can drive a scene for
demonstrations and tests, but that script is not a PLC either.

## Not a throughput model

OpenFactory runs one plant, once, in real time, with a controller in the loop, and reports what
happened in that run (counts, cycle times, utilisation). It does not run thousands of randomised
trials, and its statistics describe a run; they do not predict how a design will perform.
Discrete-event simulation tools answer that question.

## Not a network service for plant networks

Its servers bind to loopback by default. Modbus TCP has no authentication, and the OPC UA server is
anonymous by default. Keep it on a lab machine or an isolated lab network, never on a plant or
business network. See [the security policy](https://github.com/HuntIntegrativeSolutions/OpenFactory/blob/main/SECURITY.md).

## Not a finished engineering deliverable

The L5X tag stubs, Ignition tag exports, Perspective faceplate, GRAFCET charts and I/O lists it
generates are starting points for training and demos. They need review by a qualified engineer
before any client or production use.

## Not a copy of a commercial simulator

OpenFactory is an original, clean-room work. It is inspired by the category of 3D PLC trainers,
not by any product's code, assets, names or layouts, and it reads none of their files
(CONTRIBUTING.md, "Clean-Room Policy").
