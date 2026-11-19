# Python 编码规范
- 所有 Python 代码必须通过 `basedpyright` 的严格模式检查，不能有任何警告或错误。
- 严禁使用裸露的泛型类（如 `list`、`dict`、`set`、`tuple`、`Callable`），必须提供完整的类型参数（例如使用 `list[str]`、`dict[str, Any]`）。
- 所有函数参数和返回值都必须包含明确的类型注解。
- 不要留下未使用的导入（unused imports）或未使用的变量。
