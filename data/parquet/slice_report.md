# TAWOS v1.1 project slice report

## Reproduction metadata

- Source archive: `TAWOS.sql.zip`
- Archive bytes: `637550449`
- Archive SHA-256: `278984f788008c58d338e1f4aa195eae8e5b15b4153e51c247659ef8465917f7`
- Effective cutoff: `2019-01-01 00:00:00`
- Effective minimum resolved tickets per person: `15`
- Effective minimum brief characters: `300`
- Effective recommended projects: `MESOS,FAB,TIMOB,DM,EVG`
- Machine-readable metadata: `slice_report.metadata.json`

Cutoff: `2019-01-01 00:00:00` (UTC-naive timestamps as stored by TAWOS).
Pre/post counts use issue creation time. The preliminary ticket-threshold screen uses only tickets created and resolved before the cutoff, reconstructs the assignee at resolution from Change_Log, and requires at least 15 such tickets in the same project. Project/key moves, explicit resolution-date mutations, and latest resolution clears are excluded; other dated resolution transitions can only move the safe boundary later. A plausible held-out brief is a resolved issue created on or after the cutoff, owned at resolution by a person meeting that threshold, with at least 300 characters of cleaned snapshot title plus description. This is an upper-bound estimate: final roster eligibility also requires a retained/indexable Stage 1 profile, and the manifest reconstructs creation-time text and may exclude edited fields. Comments are excluded because they are created after query time.

## Recommended PoC slice

The effective project selection (`MESOS`, `FAB`, `TIMOB`, `DM`, `EVG`) balances domain diversity, pre-cutoff roster depth, text/assignment coverage, and post-cutoff benchmark headroom. Together they contain 82,703 source issues, 62,554 created before cutoff, 316 project-qualified people meeting the pre-cutoff ticket threshold, and 3,594 upper-bound plausible held-out briefs before retained-profile, creation-text, deterministic sampling, and other exclusions.

| Project | Total | Resolved | Assigned % | Summary % | Description % | Assignees | First created | Last created | Pre-cutoff | Post-cutoff | People ≥15 | Plausible briefs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ALOY | 1519 | 1306 | 68.99 | 100.00 | 93.42 | 30 | 2012-05-06 20:25:25 | 2020-10-12 15:58:01 | 1426 | 93 | 5 | 1 |
| APIKIT | 886 | 849 | 85.33 | 100.00 | 100.00 | 34 | 2013-05-03 18:25:11 | 2020-01-16 14:49:40 | 786 | 100 | 8 | 0 |
| APSTUD | 8135 | 6133 | 99.40 | 100.00 | 98.76 | 15 | 2003-12-12 19:00:00 | 2018-02-25 09:43:59 | 8135 | 0 | 10 | 0 |
| BAM | 14252 | 12219 | 29.14 | 100.00 | 90.13 | 107 | 2006-04-07 00:43:22 | 2020-10-23 22:13:07 | 13439 | 813 | 30 | 125 |
| BE | 802 | 739 | 97.76 | 100.00 | 86.28 | 64 | 2016-10-02 12:40:59 | 2020-10-18 04:07:01 | 480 | 322 | 8 | 61 |
| CLI | 645 | 575 | 84.65 | 100.00 | 99.22 | 29 | 2014-07-21 20:09:25 | 2020-10-20 16:10:59 | 591 | 54 | 6 | 1 |
| CLOV | 1501 | 1501 | 55.36 | 100.00 | 81.95 | 20 | 2007-08-02 00:41:41 | 2017-01-19 13:54:17 | 1501 | 0 | 9 | 0 |
| COMPASS | 1791 | 1450 | 47.79 | 100.00 | 90.01 | 17 | 2016-03-21 14:59:36 | 2020-10-21 15:33:07 | 887 | 904 | 3 | 49 |
| CONFCLOUD | 23409 | 5247 | 29.73 | 100.00 | 98.78 | 513 | 2003-11-19 20:40:09 | 2020-10-22 21:47:59 | 20266 | 3143 | 1 | 1 |
| CONFSERVER | 42324 | 36268 | 36.63 | 100.00 | 99.02 | 422 | 2003-09-19 08:15:03 | 2020-10-23 03:08:29 | 40140 | 2184 | 102 | 78 |
| CWD | 4311 | 3325 | 39.90 | 100.00 | 92.51 | 105 | 2006-09-25 20:37:05 | 2020-10-20 17:49:54 | 4054 | 257 | 17 | 5 |
| CXX | 2032 | 1770 | 70.77 | 100.00 | 83.17 | 39 | 2010-02-01 10:00:17 | 2020-10-21 15:17:05 | 1648 | 384 | 15 | 23 |
| DAEMON | 313 | 283 | 100.00 | 100.00 | 100.00 | 5 | 2016-02-03 23:32:09 | 2020-04-22 17:46:25 | 260 | 53 | 2 | 32 |
| DATACASS | 798 | 763 | 99.50 | 100.00 | 64.66 | 10 | 2013-05-29 12:06:51 | 2020-10-17 17:48:00 | 600 | 198 | 5 | 45 |
| DM | 26506 | 22570 | 90.12 | 100.00 | 88.13 | 211 | 2014-03-04 09:37:43 | 2020-10-22 16:40:10 | 16613 | 9893 | 105 | 1855 |
| DNN | 10060 | 7565 | 25.13 | 100.00 | 98.75 | 33 | 2012-01-03 00:39:00 | 2018-06-12 20:53:19 | 10060 | 0 | 20 | 0 |
| DURACLOUD | 1125 | 913 | 65.16 | 100.00 | 92.09 | 12 | 2009-11-11 16:05:21 | 2020-09-15 16:57:07 | 1065 | 60 | 4 | 13 |
| EVG | 10299 | 9284 | 84.99 | 100.00 | 77.43 | 67 | 2013-04-10 16:09:14 | 2020-10-21 22:09:03 | 6147 | 4152 | 21 | 465 |
| FAB | 13682 | 12842 | 77.43 | 100.00 | 83.82 | 457 | 2016-07-28 06:40:02 | 2020-10-20 08:33:34 | 9393 | 4289 | 62 | 720 |
| FE | 5533 | 4615 | 34.32 | 100.00 | 89.95 | 74 | 2006-01-11 07:21:26 | 2020-10-22 18:12:59 | 5400 | 133 | 26 | 2 |
| INDY | 2321 | 1964 | 65.06 | 100.00 | 89.06 | 59 | 2017-04-19 01:41:08 | 2020-04-16 19:02:42 | 1894 | 427 | 0 | 0 |
| IS | 1531 | 1293 | 69.37 | 100.00 | 80.34 | 92 | 2017-05-24 23:18:54 | 2020-10-05 05:38:37 | 1107 | 424 | 8 | 101 |
| JAVA | 3560 | 3446 | 65.39 | 100.00 | 88.43 | 35 | 2009-04-17 21:00:51 | 2020-10-15 12:31:38 | 2883 | 677 | 7 | 85 |
| JRACLOUD | 25669 | 5798 | 13.99 | 100.00 | 99.54 | 557 | 2002-03-21 01:22:51 | 2020-10-22 23:35:18 | 22362 | 3307 | 8 | 4 |
| JRASERVER | 44165 | 35215 | 21.76 | 100.00 | 98.66 | 462 | 2002-02-08 04:45:00 | 2020-10-23 20:38:11 | 41532 | 2633 | 70 | 25 |
| JSWCLOUD | 11702 | 7551 | 9.65 | 100.00 | 90.38 | 211 | 2007-06-14 07:25:23 | 2020-10-23 00:46:38 | 9999 | 1703 | 12 | 3 |
| JSWSERVER | 12862 | 10824 | 12.88 | 100.00 | 88.52 | 182 | 2007-06-14 07:25:23 | 2020-10-23 08:03:06 | 12043 | 819 | 14 | 0 |
| MDL | 66741 | 52249 | 73.34 | 100.00 | 98.21 | 554 | 2002-04-25 12:58:42 | 2020-10-22 11:15:06 | 61472 | 5269 | 142 | 1175 |
| MESOS | 10157 | 7091 | 64.30 | 100.00 | 92.97 | 252 | 2011-02-16 23:12:40 | 2020-10-19 16:53:27 | 9470 | 687 | 67 | 173 |
| MULE | 11816 | 11292 | 84.44 | 100.00 | 96.25 | 146 | 2004-03-23 02:49:11 | 2020-06-09 21:41:05 | 11809 | 7 | 53 | 0 |
| MXNET | 1404 | 311 | 51.57 | 100.00 | 65.38 | 50 | 2017-11-02 15:55:21 | 2020-07-14 11:44:41 | 1240 | 164 | 1 | 0 |
| NEXUS | 9912 | 9033 | 41.92 | 100.00 | 90.50 | 82 | 2008-04-03 15:47:48 | 2020-10-20 16:23:45 | 8848 | 1064 | 30 | 101 |
| SERVER | 48663 | 43834 | 91.39 | 100.00 | 87.83 | 452 | 2009-04-08 08:01:26 | 2020-10-21 20:44:31 | 35964 | 12699 | 153 | 3718 |
| STL | 1663 | 1157 | 93.81 | 100.00 | 69.69 | 56 | 2016-07-18 18:01:57 | 2020-09-23 07:22:54 | 1427 | 236 | 16 | 35 |
| TIDOC | 3059 | 2774 | 99.64 | 100.00 | 93.07 | 62 | 2011-04-15 03:34:53 | 2020-09-29 12:46:30 | 2961 | 98 | 6 | 18 |
| TIMOB | 22059 | 19574 | 90.83 | 100.00 | 94.66 | 161 | 2011-03-29 16:48:47 | 2020-10-23 10:17:14 | 20931 | 1128 | 61 | 381 |
| TISTUD | 5979 | 5152 | 99.70 | 100.00 | 98.38 | 63 | 2011-03-01 11:50:44 | 2020-10-21 15:05:09 | 5921 | 58 | 12 | 17 |
| USERGRID | 1339 | 811 | 55.41 | 100.00 | 74.46 | 37 | 2013-11-18 14:42:00 | 2019-05-26 22:36:32 | 1333 | 6 | 8 | 0 |
| XD | 3707 | 3046 | 70.35 | 100.00 | 85.51 | 31 | 2013-04-12 06:37:17 | 2018-11-16 15:57:31 | 3707 | 0 | 16 | 0 |
