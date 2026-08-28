# LinkedIn 科普文｜Samba 與 Tango 的最後一週：用公開 TLE 能把衛星再入估到多準？

> 互動版故事：https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango
> 建議標籤：#SpaceSituationalAwareness #SSA #Reentry #ESA #Cluster #OrbitalMechanics #OpenData #SatDashboard

---

## 繁體中文版

**兩顆衛星，相隔 24 小時，同一支觀測團隊——這是 ESA 為了一份稀有的再入資料而安排的「約會」。**

2000 年發射的 ESA Cluster 四顆孿生磁層衛星，在 2024 年由 Salsa 打頭陣完成史上第一次「目標式再入」（targeted reentry）觀測後，最後兩顆 Samba 與 Tango 也走到了旅程終點。ESA 在今年 1 月各做了一次小幅點火：Samba 的再入點略向東、Tango 略向西，讓兩次再入相隔約 24 小時，飛機來得及回基地加油、輪休，再飛出去看第二場。ESA 的預報：Samba 2026-08-31 21:42 UTC、Tango 09-01 21:33 UTC，紐西蘭以北的南太平洋。

我做了一件事：**只用公開 TLE，不碰任何任務遙測，看看能把再入估到多準。**

**階段一｜SGP4 近地點掠過。** Cluster 的軌道遠地點 13 萬公里、每 53.5 小時才掠過一次大氣邊緣，再入就發生在其中一次掠過。用最後一筆 TLE 外推、逐圈找近地點：Samba 08-31 22:58 UTC、Tango 09-01 22:04 UTC——**圈次與 ESA 完全一致**，但時間差 +1.3 h／+0.5 h。對再入而言，1 小時的時間誤差就是 15° 經度。

**階段二｜數值傳播＋Monte Carlo。** J2、太陽與月球引力、NRLMSIS 2.1 大氣阻力、大氣共轉，對迎風面積（3.8–6.6 m²）與密度尺度抽樣。密度尺度先拿 2024 年 Salsa 的真實再入（09-08 18:47 UTC）回測校準：1 天前置誤差 −0.03 h、13 天前置 −1.3 h。套到 Samba／Tango：Samba 08-31 19:35 UTC（−30.5°, −128.5°）、Tango 09-01 20:46 UTC（−30.4°, −145.3°），與 ESA 差 −2.1 h／−0.8 h。

**對照組｜美軍 18 SDS 的 60 日衰減預報**（Space-Track）：Samba 09-14、Tango 09-01——日級精度，對高橢圓軌道的最後幾圈明顯力不從心。

三個結論：
1. 公開 TLE 足以判斷「哪一圈再入」與大致海域，但要壓到分鐘級，需要的是**新鮮的軌道決定**（Samba 最後一筆 TLE 已是 15 天前）與實測，而非更好的模型。
2. 高橢圓軌道的「衰減」由月日攝動主導，近圓軌道的 Δv≈n·Δa/2 這類直覺完全不適用——Samba 1 月那次 +45 km 的半長軸跳變，在 TLE 上看得到，但 TLE 無法獨立證明點火。
3. 誠實標示比漂亮數字重要：故事裡每個數字都標了 [ESA-reported]、[TLE-derived] 或 [interpretation]，並附 claim-to-source 對照表。

8 月 31 日晚上，Samba 會先走；隔天輪到 Tango。到時候我會把 ESA 的實際觀測結果貼回同一頁，讓預報與真值面對面。

👉 完整互動故事（軌道演化、點火簽章、再入估算 vs ESA、來源表）：
https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango

---

## English version

**Two satellites, 24 hours apart, one observation team — ESA's carefully arranged "appointment" for a rare set of reentry data.**

ESA's four identical Cluster satellites were launched in 2000. Salsa led the way in 2024 with the first-ever *targeted reentry* observation; now the last two, Samba and Tango, are reaching the end of the road. In January ESA performed one small burn on each: Samba's reentry point nudged east, Tango's west, so the two reentries fall roughly 24 hours apart — enough for the aircraft to return to base, refuel, rest, and fly out again. ESA's forecast: Samba 2026-08-31 21:42 UTC, Tango 09-01 21:33 UTC, over the South Pacific north of New Zealand.

So I asked a simple question: **using nothing but public TLEs — no mission telemetry — how close can we get?**

**Stage 1 | SGP4 perigee passes.** Cluster's orbit reaches 130,000 km at apogee and only grazes the atmosphere once every 53.5 hours; reentry happens on one of those grazes. Propagating the last TLE and listing perigee passes: Samba 08-31 22:58 UTC, Tango 09-01 22:04 UTC — **the same pass ESA predicts**, but +1.3 h / +0.5 h off. For reentry, one hour of timing error is 15° of longitude.

**Stage 2 | Numerical propagation + Monte Carlo.** J2, Sun and Moon, NRLMSIS 2.1 drag with a co-rotating atmosphere, sampling the frontal area (3.8–6.6 m²) and a density scale factor. The density scale was first calibrated by hindcasting Salsa's real reentry (2024-09-08 18:47 UTC): −0.03 h error with a 1-day lead, −1.3 h with a 13-day lead. Applied to Samba/Tango: Samba 08-31 19:35 UTC (−30.5°, −128.5°), Tango 09-01 20:46 UTC (−30.4°, −145.3°) — −2.1 h / −0.8 h versus ESA.

**Control group | the US 18 SDS 60-day decay forecast** (Space-Track): Samba 09-14, Tango 09-01 — day-level precision that clearly struggles with the final revolutions of a highly eccentric orbit.

Three takeaways:
1. Public TLEs are enough to identify *which pass* and roughly *which ocean*, but getting to minute-level needs **fresh orbit determination** (Samba's last TLE is 15 days old) and real tracking — not a better model.
2. Decay in a highly eccentric orbit is driven by lunisolar perturbations; near-circular intuition like Δv ≈ n·Δa/2 simply does not apply. Samba's +45 km semi-major-axis jump in January is visible in the TLEs, yet TLEs alone cannot prove a burn.
3. Honest labelling beats pretty numbers: every figure in the story is tagged [ESA-reported], [TLE-derived] or [interpretation], with a claim-to-source table.

Samba goes first on the evening of 31 August; Tango follows the next day. When ESA publishes what the aircraft actually saw, I'll put it on the same page — forecast and truth, side by side.

👉 Full interactive story (orbit evolution, burn signatures, reentry estimate vs ESA, sources):
https://rhynowu-atrdc-satdashboard.hf.space/story/cluster-samba-tango

*All figures above are TLE-derived estimates from an open-data dashboard, not operational reentry predictions. ESA forecast values: ESA Rocket Science blog, 2026-08-24.*
