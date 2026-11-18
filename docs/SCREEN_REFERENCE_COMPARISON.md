# Rechecking the published screen references

The six complete v1.4.0 and v1.5.0 recordings have a separate set of source
references. The [independent source agreement](data/v160-historical-label-agreement.json)
was 249 matched boundaries across 258 and 250 proposals (F1 98.03%) and 0 edits
across 1,628 characters in five common crops.

These scores reuse the v1.5.0 packs and the same one-to-one scorer. The new
references change the evaluated onsets and expand code coverage from 15 to
41 regions. The original release scores remain in the archive.

| v1.5.0 extraction | References | Changes captured | Information precision | Code CER |
| --- | --- | ---: | ---: | ---: |
| Mac, published | Original | 169/182 (92.86%) | 169/499 (33.87%) | 935/4,165 (22.45%) |
| Mac, same packs | Revised | 171/179 (95.53%) | 171/499 (34.27%) | 4,109/7,083 (58.01%) |
| Docker, published | Original | 173/182 (95.05%) | 173/693 (24.96%) | 549/4,165 (13.18%) |
| Docker, same packs | Revised | 170/179 (94.97%) | 170/693 (24.53%) | 3,861/7,083 (54.51%) |

Using nominal one-second crop times produced 4,372 Mac edits and 4,190 Docker
edits, each out of 7,083 characters. Some one-second samples depict the next
page while the integer timestamp still points to the previous one. The final
revised scores use the exact presentation timestamps verified in the
[source-frame record](data/v160-v150-native-time-evidence.json). With those
times, the expanded set still exposes missed or incorrect text in the retained
images.

[Revised labels](data/v160-v150-reviewed-labels.json) ·
[Mac scores](data/v160-v150-reviewed-mac-results.json) ·
[Docker scores](data/v160-v150-reviewed-docker-results.json) ·
[Original results](SCREEN_QUALITY.md#new-holdout)
