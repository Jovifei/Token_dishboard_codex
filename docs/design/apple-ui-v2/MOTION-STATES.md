# UI v2 动效状态对照

| State | 交互 | 视觉与行为 |
|---|---|---|
| Rest | 不操作 | 灰色或品牌蓝按钮，无阴影下压 |
| Hover | 鼠标进入 | 120ms 表面提亮，文案及几何尺寸不变 |
| Press | 左键按下 | 再轻度提亮，**不凹陷、不缩放、不下移** |
| Release | 松开 | 回到 Hover，业务动作即刻执行 |
| Focus | 键盘 Tab | 保留 ttk 焦点语义，不依赖鼠标 |
| Disabled | 前置不满足 | 灰化、不执行 |
| Reduce Motion | `QINGXU_REDUCE_MOTION=1` | 不过渡，仅即时更色 |

本轮没有制作或导出 Figma Motion 轨道；代码动效使用 Tk `after()`，每次仅在原有按钮样式上做颜色插值。
