# claude-boba 🧋

同事之間的 **Claude 代跑互助平台**。

額度用完的人把手上的對話丟進來，還有額度的同事用**自己的帳號、在自己的機器上**代為跑完，
結果交還。系統算出這次代跑在外面要花多少錢，換算成「請喝飲料 / 請吃飯」的人情債。

**不收錢，只記人情。**

---

## 現在的狀態

**設計完成，尚未實作。** 所有決策與理由寫在 [SPEC.md](SPEC.md)。

下一步是 SPEC.md §11 的 Phase 0 spike —— 有六項未驗證的假設，其中兩項失敗會直接推翻設計，
在驗完之前寫任何功能程式碼都有白工的風險。

## 這不是什麼

- **不是帳號共享平台。** 憑證從頭到尾不離開出租者的機器。
  Anthropic 官方沒有任何支援「A 把訂閱額度給 B」的機制，分享憑證會違反使用條款，
  而且風險非對稱 —— 被停權的是出租者。
- **不是付費市集。** 不收錢、不轉帳。
- **不是公司正式系統。** Side project。

## 開發環境

```bash
python3 -m venv .venv
.venv/bin/pip install ruff==0.16.1 pre-commit
.venv/bin/pre-commit install
```

`ruff` 釘在 0.16.1 —— 跟 `lighthouse-saas-api` 同一版，換版本會格式化出不同結果。

## 給 agent 的說明

見 [CLAUDE.md](CLAUDE.md) 與 [.claude/rules/](.claude/rules/)。
