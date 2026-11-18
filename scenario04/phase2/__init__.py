"""第二階段：會合評估、避碰決策與 RPO 控制模擬（Sprint 0：規格與測試骨架）。

規格依據：第二階段規劃 v0.3（框架與時間規格、協方差資料契約、適用性篩選、分層驗收）。
Rust 單機版 src/phase2/ 為同一套演算法的移植，兩邊以 testdata/ 共用測試資料比對。

模組：
  constants   凍結的物理常數與數值設定
  eop         地球定向參數（CelesTrak EOP-All.csv）讀取與內插
  timescale   UTC → TAI → TT、UT1
  frames      TEME / GCRF / ITRF 狀態與協方差轉換（IAU-2006/2000A，CIO 法）
  rtn         RTN 軸與協方差速度語意
  forces      二體＋J2–J4 帶諧項（ITRF 計算）＋阻力
  integrator  固定步長 RK4 與五次 Hermite 內插星曆
  kepler      二體解析解（測試與跨語言演算法層比對用）
  tca         近接極小值搜尋（g(t)=r·v 求根＋二階條件）
  pc          Foster 2D／Chan 碰撞機率
  screening   二維 Pc 適用性啟發式篩選（三態）
  schema      場景 JSON 驗證
  e2e         最小端到端案例
"""
