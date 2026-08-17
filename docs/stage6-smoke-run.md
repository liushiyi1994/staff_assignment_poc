# Stage 6 query engine — authorized smoke run

- Run: 2026-08-11, branch `agent/stage6-query`, work order
  `docs/work-orders/stage6-query-engine.md`
- Models: `openai/gpt-5.6-terra` for intent and re-rank (OpenRouter),
  local `BAAI/bge-small-en-v1.5` for the vector arm
- Stage name `stage6_pilot`; spend **$0.1649** across 10 calls (5 intent, 5
  re-rank, no retries), against the $1.00 `llm.pilot_budget_usd` ceiling
- Graph: the Stage 5 load accepted on 2026-08-11 (316 Person, 2,666
  Contribution, 10,630 Skill, 344 Specialization)

## Result against the acceptance criteria

| criterion | result |
|---|---|
| every re-rank reason cites evidence keys in that person's own contributions | **met** — 293 keys cited across 74 shortlisted people, 0 foreign, 0 uncited; one real violation was caught and rejected (brief 3) |
| at least one shortlist contains a vector-arm-only person | **met** — briefs 2, 3 and 4 (15 people in total; brief 3 alone has 11) |
| smoke spend within the $1 ceiling under `stage6_pilot` | **met** — $0.1649 |
| end-to-end latency < 15 s per brief | **not met** — 16.79–21.63 s. The engine's own work is 0.12–0.31 s; a single re-rank generation is 15.1–20.0 s of every brief. See below. |

## Latency breakdown

| brief | wall | intent | retrieval | re-rank | spend |
|---|---|---|---|---|---|
| 1 backend / containers | 19.53 s | 1.8 s | 2.31 s | 15.5 s | $0.0323 |
| 2 CI/CD and build tooling | 16.83 s | 1.4 s | 0.29 s | 15.1 s | $0.0334 |
| 3 distributed ledger | 16.79 s | 1.4 s | 0.12 s | 15.3 s | $0.0319 |
| 4 mobile SDK | 21.63 s | 1.4 s | 0.24 s | 20.0 s | $0.0343 |
| 5 scientific data pipeline | 17.84 s | 1.4 s | 0.16 s | 16.3 s | $0.0331 |

Brief 1's 2.31 s retrieval includes the one-time embedding-model load; every
later brief in the same process retrieves in 0.12–0.29 s. The miss is
generation-bound: a re-rank answers for 15 people and emits 1,506–1,917 output
tokens. Nothing in the retrieval, scoring, or graph path is close to the
budget. Options — each a design or authorization decision, so escalated rather
than chosen here — are recorded in the worker report: shorten the required
reason, lower `retrieval.rerank_top_k` (the eval needs at least 10 ranked
people for Hit@10), route the re-rank to a faster model, or accept the number
for a research PoC.

## Transcripts

### Brief 1

```
$ uv run python -m capgraph.query.engine "Need a backend engineer with deep container orchestration and Docker integration experience"
```

Parsed intent — domain `backend container infrastructure`, 1 role(s), recency_years `None`.

**Role:** backend engineer (need 1)  
**Specializations asked:** Container orchestration, Kubernetes / container orchestration, Container image provisioning  
**Skills asked:** container orchestration, Docker integration  
**Candidates:** vector 25, structured 38, vector-only 7, union 45, re-ranked 15, shortlisted 15

| # | person | score | fit | found by | matched terms | reason | evidence |
|---|---|---|---|---|---|---|---|
| 1 | Person MESOS-3406 | 0.483 | strong | vector+structured | Container orchestration | Has deep, recent container-orchestration backend evidence, with eight Mesos containerizer and five Docker runtime-isolator records through 2018, including Docker image provisioning, private-registry pulls, and runtime configuration integration. | MESOS-4240, MESOS-4226, MESOS-7251, MESOS-6653 |
| 2 | Person MESOS-3415 | 0.483 | strong | vector+structured | Container orchestration | Brings 12 container-orchestration evidence records through 2018 plus recent Docker Containerizer work covering image acquisition, registry fetching, mount behavior, and Docker executor issues. | MESOS-4263, MESOS-4291, MESOS-9279, MESOS-8356 |
| 3 | Person MESOS-3664 | 0.583 | strong | structured | Container image provisioning, Container orchestration | Has direct Mesos containerizer and Docker integration experience, including Docker image-store and provisioner work in 2018 and container recovery, executor lifecycle, and Docker networking work. | MESOS-3004, MESOS-3021, MESOS-4241, MESOS-2115 |
| 4 | Person MESOS-3360 | 0.467 | strong | vector+structured | Container orchestration | Has eight recent container-orchestration records and hands-on Docker executor/container-runtime work involving lifecycle failures, CNI networking, cgroups, and container isolation through 2018. | MESOS-9231, MESOS-8876, MESOS-9039, MESOS-9152 |
| 5 | Person MESOS-3500 | 0.487 | good | vector+structured | Container image provisioning, Container orchestration | Has recent Mesos orchestration backend work on containerizer testing, image-pull latency, image garbage collection, and Docker Containerizer CFS-quota support. | MESOS-8884, MESOS-8075, MESOS-8090, MESOS-6134 |
| 6 | Person DM-145758 | 0.511 | good | structured | Container orchestration, Kubernetes / container orchestration | Provides direct Kubernetes and Docker deployment experience, with five Kubernetes and six Docker evidence records covering GKE StatefulSets, persistent volumes, Docker builds, registries, and cluster networking. | DM-15808, DM-16014, DM-15116, DM-13837 |
| 7 | Person MESOS-3737 | 0.415 | good | vector+structured | Container orchestration | Has direct container-orchestration and Docker integration experience through Docker Volume Driver isolator implementation, Docker runtime-isolator diagnostics, image pulling, and force-pull-image support. | MESOS-5013, MESOS-5083, MESOS-5104, MESOS-4874 |
| 8 | Person MESOS-3947 | 0.549 | good | structured | Container image provisioning, Container orchestration | Has recent container-image provisioning expertise including authenticated Docker Registry manifest and layer fetching, digest verification, and Mesos containerizer integration coverage. | MESOS-3222, MESOS-3288, MESOS-3427, MESOS-4115 |
| 9 | Person MESOS-3374 | 0.400 | good | structured | Container orchestration | Has recent backend container-runtime evidence for nested-container lifecycle reliability and Docker executor/containerizer failure handling, including hung docker-inspect and terminal-status issues. | MESOS-8732, MESOS-8577, MESOS-8574, MESOS-8258 |
| 10 | Person MESOS-3899 | 0.415 | good | vector+structured | Container orchestration | Has direct Mesos Docker and provisioning experience, including Docker image-pull failure analysis, Docker container environment handling, and a writable OverlayFS provisioner backend. | MESOS-4587, MESOS-4249, MESOS-4571, MESOS-2971 |
| 11 | Person MESOS-3408 | 0.439 | related | vector+structured | Container orchestration | Has recent container-orchestration backend and containerizer evidence, including local Docker image-puller diagnostics and private-registry layer lookup investigation, but Docker integration is not a primary repeated skill. | MESOS-8140, MESOS-9367, MESOS-8440, MESOS-9300 |
| 12 | Person MESOS-3491 | 0.400 | related | structured | Container orchestration | Has backend container-orchestration experience in Mesos containerizer extensibility, Linux network namespaces, and container IP binding, but the profile does not show direct Docker integration evidence. | MESOS-2884, MESOS-3133, MESOS-3361, MESOS-3039 |
| 13 | Person DM-145831 | 0.511 | related | vector+structured | Container orchestration, Kubernetes / container orchestration | Has Kubernetes cluster-support and security-architecture experience, including network policies, firewall configuration for container access, and development-cluster node management, but lacks direct Docker integration evidence. | DM-15242, DM-15595, DM-14622, DM-16354 |
| 14 | Person DM-145927 | 0.472 | related | structured | Kubernetes / container orchestration | Has one Kubernetes-orchestration contribution covering Kubernetes manifests, liveness/readiness probes, Terraform-based cluster provisioning, and Docker packaging, but evidence depth is limited to a single period. | DM-14096, DM-13980, DM-14223, DM-14542 |
| 15 | Person FAB-144600 | 0.511 | related | vector+structured | Container image provisioning, Container orchestration | Has substantial Docker build, publishing, Compose, and multi-architecture release automation evidence, but the profile shows no direct container-orchestration specialization. | FAB-10148, FAB-10760, FAB-10882, FAB-10735 |

Latency 19.53 s (intent 1.8 s, retrieval 2.31 s, re-rank 15.5 s) · spend $0.0323 · 60 evidence keys cited, 0 foreign.

### Brief 2

```
$ uv run python -m capgraph.query.engine "Looking for someone who has built CI/CD and build-infrastructure tooling for a large test fleet"
```

Parsed intent — domain `CI/CD and test-fleet infrastructure`, 1 role(s), recency_years `None`.

**Role:** CI/CD and build infrastructure engineer (need 1)  
**Specializations asked:** DevOps / build infrastructure, Build and test infrastructure, CI orchestration and host infrastructure  
**Skills asked:** CI/CD, build-infrastructure tooling, large test fleet  
**Candidates:** vector 12, structured 40, vector-only 5, union 45, re-ranked 15, shortlisted 15

| # | person | score | fit | found by | matched terms | reason | evidence |
|---|---|---|---|---|---|---|---|
| 1 | Person EVG-148364 | 0.616 | strong | structured | Build and test infrastructure, DevOps / build infrastructure | Built Evergreen CI orchestration and host infrastructure across scheduler performance, task groups, dynamic task generation, patch-build validation, and host provisioning in 2018. | EVG-5209, EVG-5315, EVG-3600, EVG-3217 |
| 2 | Person EVG-148359 | 0.750 | strong | structured | Build and test infrastructure, CI orchestration and host infrastructure, DevOps / build infrastructure | Built Evergreen build-orchestration tooling for version generation, task generation, display-task execution, task lifecycle recovery, and spawned-host provisioning throughout 2018. | EVG-5370, EVG-5560, EVG-3231, EVG-5161 |
| 3 | Person EVG-148358 | 0.616 | strong | vector+structured | Build and test infrastructure, DevOps / build infrastructure | Built Evergreen CI backend infrastructure for agent deployment scheduling, host provisioning and decommissioning, task rescheduling, build-health workflows, and test burn-in support. | EVG-5274, EVG-3635, EVG-2106, EVG-882 |
| 4 | Person FAB-144595 | 0.666 | good | structured | Build and test infrastructure, DevOps / build infrastructure, Jenkins CI | Built and maintained Fabric CI, smoke-test, and performance-test infrastructure, including Jenkins test entry points and diagnosis of daily CI failures across large multi-peer networks. | FAB-6665, FAB-5571, FAB-10550, FAB-10515 |
| 5 | Person FAB-144441 | 0.583 | good | structured | Build and test infrastructure, DevOps / build infrastructure | Built multihost Fabric performance-test infrastructure and executed OTE/PTE scenarios with 30,000 transactions across orderers, Kafka brokers, ZooKeeper, channels, peers, and cloud hosts. | FAB-7665, FAB-7061, FAB-7081, FAB-8208 |
| 6 | Person DM-145758 | 0.700 | good | structured | Build and test infrastructure, DevOps / build infrastructure, Travis CI build troubleshooting | Built CI-generated development images, Travis integration-test support, Docker-based Qserv deployment tooling, and unit/integration-test build infrastructure for a multi-node system. | DM-11795, DM-12019, DM-7704, DM-405 |
| 7 | Person FAB-144447 | 0.573 | good | structured | Build and test infrastructure, DevOps / build infrastructure | Built PTE performance and integration-test automation with remote and multi-cloud execution, long-running multi-channel benchmarks, CI scripts, TPS reporting, and latency capture. | FAB-12185, FAB-12243, FAB-6297, FAB-3983 |
| 8 | Person FAB-144800 | 0.573 | good | vector | Build and test infrastructure, DevOps / build infrastructure | Built and troubleshot Fabric system-test and CI infrastructure using Behave, Docker Compose topologies, orderer and Kafka runners, container-log diagnostics, and expanded test networks. | FAB-4608, FAB-6397, FAB-7410, FAB-12285 |
| 9 | Person FAB-144483 | 0.573 | good | structured | Build and test infrastructure, DevOps / build infrastructure | Developed Go end-to-end and integration-test infrastructure with Ginkgo execution, Kafka runners, suite setup and parallelization, plus local and containerized CouchDB test support. | FAB-9225, FAB-9227, FAB-9712, FAB-10336 |
| 10 | Person DM-145723 | 0.684 | related | structured | Build and test infrastructure, DevOps / build infrastructure, Jenkins CI | Contributed to Jenkins dataset troubleshooting, CI test-data and release infrastructure, and reproducibility work for bleeding-edge conda builds, but most evidence is data-pipeline focused. | DM-14216, DM-16729, DM-16762, DM-15044 |
| 11 | Person DM-145759 | 0.616 | related | structured | Build and test infrastructure, DevOps / build infrastructure | Built Kubernetes and container deployment infrastructure, including cluster installation guidance, container startup troubleshooting, and an HTCondor container update, rather than CI fleet tooling directly. | DM-15017, DM-15281, DM-12836, DM-13427 |
| 12 | Person MESOS-3408 | 0.570 | related | structured | Build and test infrastructure, DevOps / build infrastructure | Worked on container-orchestration and build/test infrastructure, including fetcher metrics, container isolation, portability fixes, and test-configuration changes, but not CI/CD fleet orchestration itself. | MESOS-7778, MESOS-6575, MESOS-8128, MESOS-7987 |
| 13 | Person DM-145720 | 0.616 | related | structured | Build and test infrastructure, DevOps / build infrastructure | Contributed to build and test reliability through SCons and dependency maintenance, parallel-test failure investigation, flake8 enablement, and Sphinx documentation builds, without direct large-fleet CI ownership. | DM-3856, DM-3154, DM-13600, DM-13370 |
| 14 | Person FAB-144432 | 0.583 | related | structured | Build and test infrastructure, DevOps / build infrastructure | Performed Fabric release engineering across multiple repositories and investigated integration-test reliability, but the evidence centers on release preparation rather than CI or fleet infrastructure tooling. | FAB-13113, FAB-13122, FAB-12138, FAB-1969 |
| 15 | Person TIMOB-166083 | 0.592 | related | structured | DevOps / build infrastructure, Windows SDK build and deployment infrastructure | Worked on Windows SDK build and deployment tooling, including build performance, Babel migration, path-with-spaces compilation, and CMake project regeneration, but not large test-fleet infrastructure. | TIMOB-26331, TIMOB-25774, TIMOB-26510, TIMOB-25303 |

Latency 16.83 s (intent 1.4 s, retrieval 0.29 s, re-rank 15.1 s) · spend $0.0334 · 60 evidence keys cited, 0 foreign.

### Brief 3

```
$ uv run python -m capgraph.query.engine "Who has worked on distributed ledger transaction validation and privacy features?"
```

Parsed intent — domain `distributed ledger and blockchain privacy`, 1 role(s), recency_years `None`.

**Role:** blockchain engineer (need 1)  
**Specializations asked:** Blockchain transaction processing, Cryptographic identity and Idemix  
**Skills asked:** distributed ledger transaction validation, privacy features  
**Candidates:** vector 21, structured 3, vector-only 20, union 23, re-ranked 15, shortlisted 14

| # | person | score | fit | found by | matched terms | reason | evidence |
|---|---|---|---|---|---|---|---|
| 1 | Person FAB-144573 | 0.447 | strong | vector+structured | Cryptographic identity and Idemix | Directly combined Idemix privacy work—attribute predicates, auditing, credential signing and verification—with FabToken VSCC validation and committing-peer transaction processing in 2018. | FAB-8793, FAB-11173, FAB-9671, FAB-11351 |
| 2 | Person FAB-144588 | 0.295 | strong | vector | — | Has repeated direct transaction-validation evidence, including VSCC validation, key-level and state-based endorsement validation, and private-data validation through collection and policy checks in 2017-2018. | FAB-11948, FAB-12089, FAB-5932, FAB-5872 |
| 3 | Person FAB-144569 | 0.316 | strong | vector | — | Worked directly on distributed-ledger validation and private-data capabilities, including block-validation error handling, private-data dissemination, reconciliation, and atomic ledger/private-data commits. | FAB-5353, FAB-5533, FAB-11894, FAB-12000 |
| 4 | Person FAB-144472 | 0.405 | good | structured | Blockchain transaction processing | Has extensive 2018 private-data feature work spanning collection-constraint validation, collection upgrades, private-data authorization policies, old-block private-data commits, and reconciliation storage behavior. | FAB-9204, FAB-9546, FAB-11388, FAB-13039 |
| 5 | Person FAB-144471 | 0.306 | good | vector | — | Worked on Fabric ledger private-data lifecycle and validation-related behavior, including private-data metadata lookup by key hash for validation, collection eligibility, atomic commits, and phantom-read validation. | FAB-11560, FAB-11817, FAB-6552, FAB-2022 |
| 6 | Person FAB-144433 | 0.282 | good | vector | — | Contributed to private-data processing across validation, authorized-peer gossip retrieval, endorsement-aware pulling, and transient-store handling, with additional secure peer-communication work. | FAB-6199, FAB-6379, FAB-6520, FAB-7484 |
| 7 | Person FAB-144460 | 0.447 | good | structured | Blockchain transaction processing | Worked on authenticated FabToken transaction infrastructure, including redemption proofs, TMS Issuer proof computation, committing-peer processing, token capability checks, and custom transaction processors. | FAB-11941, FAB-11354, FAB-11678, FAB-12963 |
| 8 | Person FAB-144955 | 0.260 | good | vector | — | Worked on confidential chaincode and metadata confidentiality plus FabToken issue/transfer transaction-proto design, although the evidence is more security design than transaction-validator implementation. | FAB-11172, FAB-81, FAB-86, FAB-85 |
| 9 | Person FAB-144432 | 0.247 | related | vector | — | Worked primarily on release engineering and testing, with some investigation of private-data reconciliation and purged-private-data retrieval behavior. | FAB-13403, FAB-1969, FAB-10231, FAB-10235 |
| 10 | Person FAB-144477 | 0.247 | related | vector | — | Documented private-data reconciliation along with state-based endorsement and pluggable validation, but the profile shows documentation rather than implementation of transaction validation or privacy features. | FAB-11954, FAB-11915, FAB-12947, FAB-11599 |
| 11 | Person FAB-144461 | 0.247 | related | vector | — | Worked on samples and documentation involving private-data chaincode containers and transaction proposal examples, without direct validation or privacy-feature implementation evidence. | FAB-12173, FAB-12215, FAB-12749 |
| 12 | Person FAB-144595 | 0.267 | related | vector | — | Has distributed transaction and event-delivery testing experience, including multi-organization performance tests and transaction-block failure investigation, but not validation or privacy-feature development. | FAB-8199, FAB-7329, FAB-12812, FAB-10956 |
| 13 | Person FAB-144483 | 0.247 | related | vector | — | Worked on chaincode transaction-context handling and peer infrastructure, but the evidence is centered on observability, dependency wiring, and operational controls rather than validation or privacy features. | FAB-9739, FAB-9815, FAB-12363, FAB-12710 |
| 14 | Person FAB-144572 | 0.247 | related | vector | — | Has adjacent security and ledger-reliability work, including certificate revocation handling and secure TLS verification, but no direct distributed-ledger transaction-validation or privacy-feature implementation evidence. | FAB-9651, FAB-9957, FAB-9130, FAB-9664 |

Rejected re-rank entries:

- `FAB:144437: cites evidence not in this person's contributions: FAB-12177, FAB-6574, FAB-6620, FAB-6199`

Latency 16.79 s (intent 1.4 s, retrieval 0.12 s, re-rank 15.3 s) · spend $0.0319 · 55 evidence keys cited, 0 foreign.

### Brief 4

```
$ uv run python -m capgraph.query.engine "Need a mobile SDK engineer strong in iOS UI internals and event handling"
```

Parsed intent — domain `mobile SDK`, 1 role(s), recency_years `None`.

**Role:** mobile SDK engineer (need 1)  
**Specializations asked:** iOS UI and platform integration, iOS SDK/runtime debugging, Event systems and UI window behavior  
**Skills asked:** iOS UI internals, event handling  
**Candidates:** vector 14, structured 40, vector-only 9, union 49, re-ranked 15, shortlisted 15

| # | person | score | fit | found by | matched terms | reason | evidence |
|---|---|---|---|---|---|---|---|
| 1 | Person TIMOB-167543 | 0.692 | strong | structured | Event systems and UI window behavior, iOS SDK/runtime debugging, iOS UI and platform integration, Android ListView event handling, Android scrollable-view event handling, Android TableView event handling, Button touchmove event handling, Event propagation and UI event handling, Text field event handling, Titanium UI Widget/Evented hierarchy, Touch and click event handling, Video player and completion-event handling, WebView event handling | Worked directly on Titanium iOS SDK UI interaction behavior including TextField focus conflicts, touchmove handling, keyboard navigation, scroll-end events, and ScrollView zoom, with additional iOS ListView scroll/drag event specifications. | TIMOB-8185, TIMOB-5051, TIMOB-5455, TIMOB-14318 |
| 2 | Person TIMOB-166014 | 0.518 | strong | vector+structured | iOS UI and platform integration, Android scrollable-view event handling, iOS camera overlay and touch-event handling, WebView event handling | Has recent iOS SDK UI/runtime work on camera-overlay touch handling, click and scroll responsiveness, Window APIs, ListView/SearchBar behavior, and navigation-window orientation issues during 2017-2018. | TIMOB-23810, TIMOB-23666, TIMOB-25952, TIMOB-25871 |
| 3 | Person TIMOB-166944 | 0.556 | good | vector+structured | iOS SDK/runtime debugging, iOS UI and platform integration, Android TableView event handling, Android UI event handling, Event dispatch and window lifecycle, WebView evalJS | Investigated Titanium iOS UI/runtime defects involving keyboard-dismissal ScrollView sizing, navigation behavior, view-hierarchy lifecycle, and duplicate click-event delivery. | TIMOB-14938, TIMOB-15445, TIMOB-15939, TIMOB-12399 |
| 4 | Person TIMOB-166749 | 0.528 | good | vector | iOS SDK/runtime debugging, iOS UI and platform integration, Android scrollable-view event handling, Android TableView event handling, Android UI event handling, NavGroup event handling, Titanium UI Widget/Evented hierarchy | Worked on Titanium iOS UI event propagation, touch-event dispatch, window lifecycle events, and click/event delivery in TableViewRow and ScrollableView. | TIMOB-2993, TIMOB-7483, TIMOB-10751, TIMOB-3476 |
| 5 | Person TIMOB-166010 | 0.410 | good | structured | iOS UI and platform integration, Peek and pop event handling, Touch and click event handling | Worked on iOS-specific 3D Touch peek/pop event handling and investigated missing click and touchstart events on LivePhotoView, alongside native iOS UI API work. | TIMOB-20020, TIMOB-20277, TIMOB-20486 |
| 6 | Person TIMOB-166152 | 0.415 | good | vector+structured | AJAX event handling, Android ListView event handling, Android ScrollView, ListView, and TableView behavior, Android TableView event handling, Android UI event handling, iOS tab click event handling, NavGroup event handling, Picker change-event handling, SearchBar and window event handling, Ti.App.fireEvent event-payload handling, Touch and click event handling, UI event handling for transformed controls, WebView evalJS, WebView event handling | Investigated iOS UI/runtime behavior including iPad popover and navigation state, ListView long-press event sources, table-view touch coordinates, and TableView event handling. | TIMOB-5124, TIMOB-14143, TIMOB-14023, TIMOB-11384 |
| 7 | Person TIMOB-168046 | 0.400 | good | vector | iOS SDK/runtime debugging, AJAX event handling, Android orientation events, Native UI event handling, Tabbed Bar | Worked on iPhone native UI behavior including preventDefault on native elements, keyboard-toolbar lifecycle, ScrollView bounds, tabbed-bar events, and orientation propagation. | TIMOB-5427, TIMOB-195, TIMOB-281, TIMOB-311 |
| 8 | Person TIMOB-167124 | 0.508 | related | vector | iOS SDK/runtime debugging, iOS UI and platform integration, silent push and background event handling, WebView event handling | Investigated iOS modal-window crashes involving TextField and ScrollView, ListView selection behavior, and iOS UI/runtime issues in tab creation and picker handling, but has limited direct event-system evidence. | TIMOB-13712, TIMOB-13725, TIMOB-15006, TIMOB-15908 |
| 9 | Person TIMOB-166044 | 0.509 | related | vector+structured | iOS UI and platform integration, Android ScrollView, ListView, and TableView behavior, Android TableView touch handling, Tabbed Bar, Touch and click event handling, WebView event handling | Has cross-platform SDK UI investigations covering iOS tab-bar and navigation-button layout and picker change-event synchronization, although the profile is more QA and Android-oriented than iOS UI internals. | TIMOB-14200, TIMOB-16521, TIMOB-24575, TIMOB-17147 |
| 10 | Person TIMOB-166650 | 0.389 | related | structured | iOS SDK/runtime debugging, iOS UI and platform integration | Worked on iOS platform APIs including a Window/SearchBar crash, Contacts callbacks and permission responses, picker text color, and Window.close behavior, but offers little direct UI event-handling depth. | TIMOB-20265, TIMOB-20026, TIMOB-6412, TIMOB-18054 |
| 11 | Person TIMOB-166620 | 0.399 | related | structured | iOS SDK/runtime debugging, Analytics event handling, Android ScrollView, ListView, and TableView behavior, Android UI event handling, Event propagation and UI event handling, Touch and click event handling, WebView evalJS, WebView event handling | Has limited but relevant iOS UI evidence through directional swipe and multitouch support requests on native UIView instances, while most documented work is BlackBerry platform integration and debugger work. | TIMOB-9183, TIMOB-3034, TIMOB-3182 |
| 12 | Person TIMOB-166050 | 0.616 | related | vector+structured | iOS SDK/runtime debugging, iOS UI and platform integration, AJAX event handling, Analytics event handling, Deviceorientation event handling, node-ios-device | Worked on MobileWeb UI/runtime window events, orientation, layout, table views, and refresh behavior, but their stronger and more recent evidence is SDK build, CLI, and environment tooling rather than native iOS UI internals. | TIMOB-9644, TIMOB-9668, TIMOB-9693, TIMOB-9706 |
| 13 | Person TIMOB-166083 | 0.524 | related | structured | Event systems and UI window behavior, Alloy controller event handling, Android ListView event handling, EventLoop implementation, Native UI event handling, Picker change-event handling, SearchBar and window event handling, Titanium UI Widget/Evented hierarchy, Touch and click event handling, WebView evalJS | Has substantial UI event-routing evidence for the Windows Titanium implementation, including click, touchstart, touchend, and ListView itemClick behavior, but not for iOS. | TIMOB-25261, TIMOB-25239, TIMOB-25243, TIMOB-25297 |
| 14 | Person TIMOB-166600 | 0.457 | related | structured | iOS SDK/runtime debugging, Android ListView event handling, Android TableView touch handling, Android UI event handling, Event dispatch and window lifecycle, Text field event handling, Touch and click event handling, WebView evalJS, Window close and focus event handling | Worked extensively on Android SDK UI behavior and event handling such as ScrollView callbacks, gesture input, activity lifecycle events, and TableView long-click behavior, not iOS UI internals. | TIMOB-7872, TIMOB-17565, TIMOB-16279, TIMOB-16489 |
| 15 | Person FAB-144477 | 0.392 | related | structured | Chaincode | The profile documents Hyperledger Fabric technical documentation for peer operations, discovery, private data, and upgrade guidance, with no mobile SDK or iOS UI evidence. | FAB-12177, FAB-11599, FAB-11807, FAB-7461 |

Latency 21.63 s (intent 1.4 s, retrieval 0.24 s, re-rank 20.0 s) · spend $0.0343 · 58 evidence keys cited, 0 foreign.

### Brief 5

```
$ uv run python -m capgraph.query.engine "Someone with scientific data pipeline and astronomy image-processing background"
```

Parsed intent — domain `scientific data processing and astronomy`, 1 role(s), recency_years `None`.

**Role:** scientific data pipeline and astronomy image-processing engineer (need 1)  
**Specializations asked:** Data pipeline engineering, Astronomical image processing  
**Skills asked:** scientific data pipeline, astronomy image-processing  
**Candidates:** vector 19, structured 40, vector-only 3, union 43, re-ranked 15, shortlisted 15

| # | person | score | fit | found by | matched terms | reason | evidence |
|---|---|---|---|---|---|---|---|
| 1 | Person DM-145772 | 0.750 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Built and stackified LSST DCR coadd and matched-template image-processing pipelines through 2018, including DCR assembly, subfilter coadds, image differencing, and multiband measurement integration. | DM-15638, DM-16347, DM-9613, DM-14134 |
| 2 | Person DM-145719 | 0.750 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Has repeated recent astronomical CCD-processing pipeline work covering DECam calibration products, ISR, source detection, astrometry, and brighter-fatter correction through 2018. | DM-5767, DM-6039, DM-13293, DM-15756 |
| 3 | Person DM-145848 | 0.712 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Has direct astronomical image-processing and pipeline evidence through 2018, including coadd processing, PSF-quality input selection, deblending, shear analysis, and metacalibration workflows. | DM-14991, DM-9855, DM-10236, DM-12539 |
| 4 | Person DM-145716 | 0.750 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Worked extensively on LSST/HSC scientific data-processing pipelines, with concrete astronomical image-processing work on coadds, HSM shape measurements, simulated-source insertion, deblending, and measurement tasks. | DM-3703, DM-10849, DM-12419, DM-15336 |
| 5 | Person DM-145775 | 0.750 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Combines deep data-pipeline experience with HSC/LSST image-processing and coadd QA work, including HSC reprocessing, calibration-flag propagation, background subtraction, cosmic-ray modeling, and source measurement fixes. | DM-14874, DM-6816, DM-4998, DM-5107 |
| 6 | Person DM-145726 | 0.750 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Has substantial LSST science-pipeline and image-processing experience spanning coaddition, detection, deblending, PSF and aperture-correction measurement behavior, forced photometry, and shear-measurement tooling. | DM-4768, DM-10145, DM-1133, DM-3549 |
| 7 | Person DM-145940 | 0.617 | strong | structured | Astronomical image processing, Data pipeline engineering | Has concentrated astronomical difference-imaging pipeline experience, implementing spatially varying ZOGY and A&L decorrelation processing with PSF modeling, image-stamp mapping, and simulation-based validation. | DM-8145, DM-10508, DM-8812, DM-6241 |
| 8 | Person DM-145748 | 0.690 | strong | vector+structured | Astronomical image processing, Data pipeline engineering | Has direct LSST astronomical image-processing pipeline experience in image warping, astrometry, ISR/calibration separation, modular ProcessCcdTask design, and WCS/optical-distortion modeling. | DM-4692, DM-4063, DM-3670, DM-1969 |
| 9 | Person DM-145762 | 0.734 | good | structured | Astronomical image processing, Data pipeline engineering | Developed the jointcal astronomical photometric-calibration pipeline through 2018, integrating calibration results with coadd and warp processing and working on astrometric models and SkyWcs persistence. | DM-16305, DM-16235, DM-13669, DM-10524 |
| 10 | Person DM-145747 | 0.550 | good | vector+structured | Data pipeline engineering | Ran and maintained recent HSC/LSST scientific processing workflows across single-frame, coadd, multiBand, jointcal, pipe_analysis, and validate_drp pipelines. | DM-16099, DM-16108, DM-14689, DM-14339 |
| 11 | Person DM-145766 | 0.750 | good | vector+structured | Astronomical image processing, Data pipeline engineering | Contributed to LSST imaging pipeline infrastructure and image-analysis tasks involving camera geometry, FITS/exposure metadata, photometric calibration, astrometry, SIP distortion handling, and detector integration. | DM-9990, DM-823, DM-735, DM-16385 |
| 12 | Person DM-145723 | 0.550 | good | vector+structured | Data pipeline engineering | Worked on observatory imaging pipeline components including ISR corrections, Exposure metadata and background models, processCcd integration, image-differencing simulation/testing, and astrometry-task redesign. | DM-8635, DM-5058, DM-5758, DM-3801 |
| 13 | Person DM-145887 | 0.695 | good | structured | Astronomical image processing, Data pipeline engineering | Built and evolved CTIO calibration-telescope processing workflows for slitless astronomical images, including flatfielding, cosmic/hot-pixel flagging, spectral extraction, atmospheric modeling, and observatory calibration analysis. | DM-9992, DM-9356, DM-16349, DM-14043 |
| 14 | Person DM-145790 | 0.682 | related | vector+structured | Astronomical image processing, Data pipeline engineering | Has astronomy data-pipeline exposure through HSC COSMOS processing and Synpipe fake-source workflows, but the primary recent work is star-galaxy machine-learning classification rather than image-processing pipeline engineering. | DM-13087, DM-14109, DM-15920, DM-15926 |
| 15 | Person DM-145721 | 0.550 | related | structured | Data pipeline engineering | Has strong recent generic data-pipeline infrastructure experience in Gen3 Butler, PipelineTask, QuantumGraph, and SuperTask execution, but no direct astronomical image-processing evidence in the supplied profile. | DM-15686, DM-16077, DM-15049, DM-13958 |

Latency 17.84 s (intent 1.4 s, retrieval 0.16 s, re-rank 16.3 s) · spend $0.0331 · 60 evidence keys cited, 0 foreign.
