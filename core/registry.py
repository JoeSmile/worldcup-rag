# registry.py
class ToolRegistry:
    def __init__(self):
        self._tools = {}
    
    def register(self, name: str, tool_callable, metadata: dict):
        """注册一个工具，支持热插拔"""
        self._tools[name] = {
            "callable": tool_callable,
            "metadata": metadata  # 包含 cost, latency, provider 等
        }
    
    def get_tool(self, name: str):
        return self._tools[name]["callable"]
    
    def list_tools(self):
        return list(self._tools.keys())

# 初始化全局注册表
tool_registry = ToolRegistry()

# 注册真实工具（现在可以用假的 Mock）
tool_registry.register("sql_query", mock_sql_tool, {"provider": "postgres", "cost": 0.001})
tool_registry.register("semantic_search", mock_vector_tool, {"provider": "free_travly", "cost": 0})
tool_registry.register("graph_query", mock_neo4j_tool, {"provider": "neo4j", "cost": 0.002})