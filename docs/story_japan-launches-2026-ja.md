# 2026年6月〜8月 日本の衛星打ち上げ観測誌

**全地球の星空から単一衛星まで — 6つのスケールで見る軌道観測**

全地球の衛星分布から段階的にズームイン：日本の衛星 → QZSS 星座 → 今季の新規打ち上げ衛星 → 単一衛星の軌道ダイナミクス → 近接運用（RPO）。すべての表示はリアルタイム軌道データに基づく。

> 更新：2026-08-26　|　互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

### 資料口徑

| 項目 | 內容 |
|---|---|
| 資料來源 | Space-Track GP（公開 TLE）；本系統 DuckDB 每日彙整 |
| TLE epoch 範圍 | 2021-12-23 ～ 2026-08-27（31707 顆） |
| TLE 最新 epoch（≤ 匯出時） | 2026-08-27 22:02 UTC |
| TLE 資料齡 | 0.6 天（匯出時） |
| 資料庫更新 | 2026-08-28 11:14 UTC |
| 傳播模型 | SGP4/SDP4（python-sgp4 2.25） |
| 座標系 | TEME → GMST 轉 ECEF → WGS-84 經緯高（無極移／章動修正） |
| 精度等級 | 公開 TLE 級（LEO 沿軌 1–3 km/日量級增長），非精密星曆；不宜作為操作級決策依據 |
| 碰撞機率 | Chan (2008) 2-D 近似；σ_R/T/N = 100/500/100 m 為固定假設值（非 CDM 協方差），Pc 僅供排序 |
| 機動候選 | 相鄰 TLE 半長軸跳變 \|Δa\| 門檻（LEO 0.5 km、MEO/GEO 2 km，間隔 ≤5 天）之候選事件；Δv 由 Δa 以 Δv≈n·Δa/2 換算之等效值；替代解釋：TLE 品質波動、阻力模型誤差、資料缺漏 |
| 匯出時間 | 2026-08-28T12:03:24+00:00 |

## はじめに

2026年6月12日、H3-30S（F6）が種子島宇宙センターから6機の衛星を軌道に投入。8月10日には H3-22S（F9）が準天頂衛星 QZS-7 を打ち上げた。本ストーリーは全地球の視点から始まり、一機の衛星の日々の軌道変化まで段階的にズームインする。（カタログ注記：PETREL は NORAD 69503、STARS-X 親子衛星は単一物体 69502 として登録。69501 は前日打ち上げの STARLINK-37843 である。）

## ① 全地球の衛星位置分布（3D）

本システムのカタログに登録された全追跡物体のTLE 伝播による三次元位置（SGP4 ベクトル化伝播・自動回転ビュー・実高度スケール）。地表に張り付く低軌道シェルと、遠方の静止軌道リングが明瞭に確認できる。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=all
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

## ② 日本の全衛星位置分布（3D）

カタログ中の日本籍衛星すべての TLE 伝播による三次元位置：低軌道のリモートセンシング・科学衛星群と、外側の通信・測位衛星。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=country&val=%E6%97%A5%E6%9C%AC
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

## ③ QZSS（みちびき）星座の最新 TLE 伝播位置（3D）

準天頂衛星システム（QZSS／みちびき）全星座：QZS-1／1R／2／3／4／6、そして今季打ち上げの QZS-7。地球半径の約6.6倍の傾斜対地同期軌道シェルに分布する。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=constellation&val=QZSS
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

## ④ 2026年6〜8月 打ち上げ一覧

| 打ち上げ日 | 衛星名 | NORAD ID | ロケット | 射場 | 備考 |
|---|---|---|---|---|---|
| 2026-06-12 | PETREL | 69503 | H3-30S (F6) | 種子島 | 東京科学大学の海洋・地球観測衛星 |
| 2026-06-12 | STARS-X | 69502 | H3-30S (F6) | 種子島 | 静岡大学のテザー実験衛星（親子機を単一登録） |
| 2026-06-12 | BRO-22 | 69504 | H3-30S (F6) | 種子島 | 仏 UnseenLabs の電波スペクトル監視衛星 |
| 2026-06-12 | VERTECS | 69506 | H3-30S (F6) | 種子島 | 九州工業大学の宇宙背景放射観測衛星 |
| 2026-06-12 | HORN-L | 69505 | H3-30S (F6) | 種子島 | Bull Space のデブリ低減技術実証 |
| 2026-06-12 | HORN-R | 69507 | H3-30S (F6) | 種子島 | Bull Space のデブリ低減技術実証 |
| 2026-08-10 | QZS-7（みちびき7号機） | 100270 | H3-22S (F9) | 種子島 | 準天頂測位衛星。米宇宙軍 SĀCHI SDA ペイロード搭載 |

> NORAD 対応は Space-Track satcat による（2026-08-26 照会）。

## ⑤ 新規打ち上げ衛星の最新 TLE 伝播位置（打ち上げ順に巡回）

打ち上げ順に1機ずつフォーカス：6月投入の太陽同期軌道6機（約530〜570 km）、そして8月の QZS-7（約32,800 km の準天頂展開軌道）。ボタンで手動切り替えも可能。

*（互動區塊：TLE 傳播位置（3D，近即時））*
- 資料：https://rhynowu-atrdc-satdashboard.hf.space/api/story/positions?mode=ids&val=69503%2C69502%2C69504%2C69506%2C69505%2C69507%2C100270
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

## ⑥ PETREL 軌道タイムラプス（打ち上げ日から）

単一衛星へズームイン：PETREL（NORAD 69503）の2026-06-12打ち上げ以降の日次軌道要素。SMA 円形タイムチャートと軌道傾斜角／昇交点赤経／近地点引数の Spiral Polar 3面を1列に配置し、読み込み後にタイムラインを自動再生。白いリングが当日位置を示す。

*（互動區塊：逐日軌道要素時序（/orbit））*
- NORAD 69503：https://rhynowu-atrdc-satdashboard.hf.space/orbit?norad=69503&start=2026-06-12
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja#sat-petrel

## ⑦ RPO 近接運用デモ

本システムの RPO（ランデブー・近接運用）解析シーン：実カタログ事例による2衛星の相対接近3Dビューと最接近距離／衝突確率（Chan Pc）計算。

*（互動區塊：內嵌頁面）*
- 頁面：https://rhynowu-atrdc-satdashboard.hf.space/rpo
- 互動版：https://rhynowu-atrdc-satdashboard.hf.space/story/japan-launches-2026-ja

## おわりに

3万物体の全地球分布から、1機の衛星の日次軌道、そして2機の相対運動まで——同じ TLE データが6つのスケールの宇宙状況把握を支えている。

---

*本文件由 SatDashboard StoryMap 匯出（tools/export_story_md.py）。軌道數據為公開 TLE 以 SGP4 傳播之結果，屬態勢展示等級；互動圖表請開啟線上版。*
