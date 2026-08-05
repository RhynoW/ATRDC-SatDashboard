- 請用繁體中文回答

## 部署注意事項

- **本地分支是 `master`，但 `hf` remote（Hugging Face Space）實際建置的分支是 `main`，兩者已分岔。**
  - `git push hf master` 只會更新 hf 上的 `master` ref，**不會觸發 Space 重新建置**，Space 會停留在舊版且不報錯（`git push` 顯示成功，容易誤判為已部署）。
  - 正確作法：`git push hf master:main`（把本地 master 推到 hf 的 main 分支）。
  - 部署後務必用 `curl -s https://huggingface.co/api/spaces/RhynoWu/ATRDC-SatDashboard/runtime` 確認回傳的 `sha` 已更新為新 commit，且 `stage` 回到 `RUNNING`；不要只看 `git push` 沒有報錯就當作已上線。
  - `origin`（GitHub）沒有這個問題，`git push origin master` 即可。
