# 轻续 0.11.0-billing-rc1 · 计费统计界面

这组图片来自 **隔离合成数据** 的 Tk 页面截图，不是任何真实用户的账单。

实现基于 `main@2f53b196`，新功能入口：**用量概览 → 计费统计**。界面上的金额属于本地根据单价和 Token 明细计算的估算，**不等同于实际付款或订阅费用**。

## 概览与筛选

![费用概览与按日图表](screens/01-billing-overview.png)

![请求明细](screens/02-billing-requests.png)

![筛选操作](screens/03-billing-filter.png)

## 自定义单价及异常状态

![单价设置](screens/04-price-settings.png)

![请求详细拆分](screens/07-request-detail.png)

![部分价格未知](screens/08-partial-prices.png)

![无请求记录](screens/09-no-request-records.png)

## 窄窗口可用性

![小窗口顶部](screens/05-narrow-top.png)

![小窗口明细表](screens/06-narrow-table.png)

更多使用步骤见 [计费使用指南](../BILLING-USER-GUIDE.md)，价格及请求格式见 [PRICING-SOURCES.md](PRICING-SOURCES.md) 和 [REQUEST-SCHEMA.md](REQUEST-SCHEMA.md)。