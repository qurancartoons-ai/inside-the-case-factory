# Research Panel performance report

Measurements use the same synthetic project with 1,000 sources, 1,000 claims, long transcripts, and one attachment per source. Timings are local wall-clock measurements after application import.

| Metric | Before | After |
|---|---:|---:|
| Initial panel route | 14.317 ms | 0.012 ms |
| Initial HTML response | 977,323 bytes | 5,046 bytes |
| Manifest reads for initial shell | 3 | 0 |
| Provider/network calls | 0 | 0 |
| Thumbnail/preview generation | 0 ms | 0 ms |

The initial route became about 1,193 times faster (99.92% lower measured wall time), while its response became about 194 times smaller (99.48% reduction).

The bottleneck was eager rendering of every source and claim, including transcript content, combined with duplicate manifest reads and JSON parsing in the larger project page. It was not an external provider or network call.

After optimization, the route returns only an interactive shell. A 25-source page takes 7.578 ms cold and produces an 11,038-byte JSON payload; the same page takes 0.210 ms from the derived-data cache. An explicitly requested 2,000-character transcript chunk takes 4.437 ms. Thumbnail generation remains absent; attachment images use browser-native lazy loading.

Derived research pages are cached using the source manifest's nanosecond modification time and size as the cache key. A manifest change therefore invalidates its cache without rescanning the project. Heavy analysis is queued only by an explicit user action.
