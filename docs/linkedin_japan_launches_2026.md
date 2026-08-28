# LinkedIn 科普文｜From the whole sky to a single satellite: Japan's summer 2026 launches, seen through public orbit data

> Interactive story (Japanese): https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja
> Traditional Chinese: https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026
> Suggested tags: #SpaceSituationalAwareness #SSA #QZSS #H3 #JAXA #OrbitalMechanics #OpenData #SatDashboard #宇宙状況把握

---

## English version

**Six satellites on 12 June, one more on 10 August — and a way to watch all of them settle into orbit using nothing but public TLEs.**

This summer Japan's H3 flew twice from Tanegashima: H3-30S (F6) deployed six small satellites — PETREL, STARS-X, BRO-22, VERTECS, HORN-L and HORN-R — and H3-22S (F9) lifted the navigation satellite QZS-7 (Michibiki-7), which also carries a US Space Force SDA payload. I built a small "story map" that zooms from the entire catalogue down to a single spacecraft, in five observation levels:

1. **The whole sky** — ~30,000 tracked objects, propagated from public TLEs (SGP4/SDP4), drawn to true altitude scale.
2. **Japan's fleet** — every Japanese object in the catalogue: the low-orbit remote-sensing and science satellites, and the communication/navigation ring far outside.
3. **QZSS** — the Quasi-Zenith constellation. QZS-1/1R/2/4/6 fly inclined quasi-zenith orbits; QZS-3 and the new QZS-7 sit close to geosynchronous. The TLEs tell the QZS-7 deployment story on their own: GTO on 10 August (SMA 24,261 km, e 0.72), then orbit raising, and from 18 August a near-GEO orbit (SMA ≈ 42,175 km, e ≈ 0.007, i ≈ 3.0°).
4. **The new arrivals** — the six June satellites confirmed as sun-synchronous by their 97.7° inclination, now at 520–570 km. Two of them, HORN-L and HORN-R, are debris-mitigation demonstrators and are already descending on purpose: HORN-L is down to 334 km.
5. **One satellite** — PETREL (NORAD 69503), day by day since launch: semi-major axis, inclination, RAAN and argument of perigee as TLE mean-element trends.

And a sixth layer as a bonus: a two-satellite close-approach scene with R/T/N relative components — labelled explicitly as a *geometric* approach display, not a confirmed proximity operation.

A few things I learned building it:
- **Catalogue notes matter.** STARS-X is a mother–daughter tether pair catalogued as a single object (69502); NORAD 69501 is not a Japanese satellite at all but a Starlink launched the day before. The story carries these notes next to the table.
- **"Real-time" is the wrong word.** Everything here is near-real-time propagation from the latest available public TLE (typically <1 day old), not live sensor tracking — and the page says so on every panel.
- **Mean elements are not the truth.** The daily orbit trends are TLE/SGP4 model quantities; they show the shape of what's happening, not precise ephemerides.

Every page has a data-definition table (sources, TLE epochs, propagator, frame, limits) and each interactive panel links to the API that produced it.

👉 Japanese: https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja
👉 Traditional Chinese: https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

*Open-data, SGP4-level situational awareness — not operational orbit determination.*

---

## 日本語版

**6月12日に6機、8月10日にもう1機——公開 TLE だけで、それらが軌道に落ち着いていく様子を見る方法。**

この夏、日本の H3 ロケットは種子島から2回飛びました。H3-30S（F6）は PETREL・STARS-X・BRO-22・VERTECS・HORN-L・HORN-R の小型衛星6機を投入し、H3-22S（F9）は測位衛星 QZS-7（みちびき7号機、米宇宙軍の SDA ペイロードも搭載）を打ち上げました。私は、カタログ全体から1機の衛星まで5つの観測レベルでズームインしていく「ストーリーマップ」を作りました。

1. **全天** — 追跡物体約3万個を公開 TLE（SGP4/SDP4）で伝播し、実高度比で描画。
2. **日本の衛星群** — カタログ中の日本籍物体すべて：低軌道のリモートセンシング・科学衛星と、外側の通信・測位衛星のリング。
3. **QZSS** — 準天頂衛星システム。QZS-1/1R/2/4/6 は傾斜した準天頂軌道、QZS-3 と新しい QZS-7 は静止軌道に近い軌道。QZS-7 の軌道投入は TLE だけで物語れます：8月10日は GTO（軌道長半径 24,261 km、離心率 0.72）、軌道上昇を経て、8月18日以降は静止軌道近傍（軌道長半径 ≈ 42,175 km、e ≈ 0.007、傾斜角 ≈ 3.0°）。
4. **新入りたち** — 6月の6機は傾斜角 97.7° から太陽同期軌道と判定でき、現在 520〜570 km。うち HORN-L と HORN-R はデブリ低減の実証機で、すでに意図的に降下中——HORN-L は 334 km まで下がっています。
5. **1機の衛星** — PETREL（NORAD 69503）の打ち上げ以来の日次推移：軌道長半径・傾斜角・昇交点赤経・近地点引数を TLE 平均軌道要素のトレンドとして。

おまけの6層目として、2衛星の接近シーン（R/T/N 相対成分付き）——ただし「幾何学的な接近表示」であり、確認済みの近接運用ではないと明記しています。

作ってみて学んだこと：
- **カタログ注記は重要。** STARS-X は親子テザー衛星が単一物体（69502）として登録され、NORAD 69501 は日本の衛星ではなく前日に打ち上げられた Starlink。ストーリーではこれらの注記を表のすぐ隣に置いています。
- **「リアルタイム」は正しい言葉ではない。** ここにあるものはすべて、取得可能な最新の公開 TLE（通常1日未満）からの準リアルタイム伝播であり、センサーによる実時間追跡ではありません——各パネルにそう書いてあります。
- **平均軌道要素は「真値」ではない。** 日次の軌道トレンドは TLE/SGP4 モデル上の量であり、起きていることの形を示すもので、精密暦ではありません。

各ページにはデータ定義表（出典、TLE epoch、伝播モデル、座標系、制限）があり、各インタラクティブパネルは生成元の API にリンクしています。

👉 日本語版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja
👉 繁体字中国語版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026

*公開データによる SGP4 レベルの宇宙状況把握であり、運用レベルの軌道決定ではありません。*
