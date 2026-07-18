# Research Panel performance report

Measurements use the same synthetic project with 1,000 sources, 1,000 claims, long transcripts, and one attachment per source. Timings are local wall-clock measurements after application import.

| Metric | Before | After |
|---|---:|---:|
| Initial panel route | 14.317 ms | 0.012 ms |
| Initial HTML response | 977,323 bytes | 5,720 bytes |
| Manifest reads for initial shell | 3 | 0 |
| Provider/network calls | 0 | 0 |
| Thumbnail/preview generation | 0 ms | 0 ms |

The initial route became about 1,193 times faster (99.92% lower measured wall time), while its response became about 171 times smaller (99.41% reduction).

The bottleneck was eager rendering of every source and claim, including transcript content, combined with duplicate manifest reads and JSON parsing in the larger project page. It was not an external provider or network call.

After optimization, the route returns only an interactive shell. Sources and claims make no data request until their section is opened, and previous/next pagination keeps every page reachable. In the final verification run, a cold 25-source projection took 28.288 ms (one manifest read), a cold 25-claim projection took 2.349 ms, and the cached source projection took 0.306 ms. An explicitly requested 2,000-character transcript chunk took 66.980 ms. These on-demand timings include reading and parsing the synthetic multi-megabyte manifests; initial-route preview and thumbnail generation remains 0 ms. Attachment images use browser-native lazy loading.

Derived research pages are cached using the source manifest's nanosecond modification time and size as the cache key. A manifest change therefore invalidates its cache without rescanning the project. Heavy analysis is queued only by an explicit user action.
