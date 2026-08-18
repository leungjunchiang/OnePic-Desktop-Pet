# 用户私有素材

这里保存尚未决定是否公开的原图、标准角色候选、确认状态、动作预览和派生角色素材。

推荐先运行：

```powershell
.\scripts\start_onepic.ps1 -SourceImage "图片路径"
```

脚本会保留 `source/original.*`，并将同一张原图按原始像素尺寸写为 `selfie.png`。`workflow.json` 记录标准角色和走路是否得到用户确认，`review/` 保存需要查看的候选图和 GIF。

本目录中除本说明外的内容默认被 `.gitignore` 忽略。虚拟角色或其他拥有公开授权的素材可以在确认授权条款后复制到公开演示目录；不要直接取消整个目录的忽略规则。
