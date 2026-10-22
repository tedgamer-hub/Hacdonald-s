# 配餐请求协议

这是 Hacdonald's 自有协议，不是官方工具参数。

```json
{
  "store_code": "来自门店查询的纯数字编码",
  "budget_fen": 8000,
  "crew": [
    {"id": "成员1", "hunger": 3, "drink": "optional", "prefer": ["鸡"], "exclude_keywords": [], "avoid": []},
    {"id": "成员2", "hunger": 4, "drink": "none", "prefer": [], "exclude_keywords": [], "avoid": []}
  ]
}
```

- 总预算为 1–100000 整数分；例如 80 元是 8000，不能传浮点金额。
- crew 为 1–10 人，id 唯一；hunger 为 1–5，4–5 必须分配一份主食和一份小食，1–3 至少分配一份主食。食量仅是搭配规则。
- drink 为 optional / required / none，分别是可选 / 必须 / 不要。不能使用未知枚举值。
- prefer 为名称关键词软偏好，不保证满足；exclude_keywords 为用户明确的名称关键词硬排除，大小写不敏感，不代表配料验证。
- avoid 保留饮食禁忌或过敏要求。第一版无法验证配料，任何非空 avoid 都会返回 needs_dietary_verification，不产生“安全可食用”结论。
- 不得以未知字段替代真实成员要求。保存需求时不要添加 endpoint、Token、联系方式或支付信息。

返回 plans 的各 assignment 包含该成员餐品列表，每条代表一份；items 汇总全队商品数量。套餐子项、特调、营养、优惠券与分账尚未进入当前求解器。
