# security.py
class SecurityPipeline:
    def __init__(self):
        self._filters = []
    
    def add_filter(self, filter_func):
        self._filters.append(filter_func)
    
    def process(self, query: str):
        for filter_func in self._filters:
            query = filter_func(query)
        return query

# 定义具体安全过滤器
def pii_anonymizer(query: str):
    # 用 Presidio 脱敏
    return anonymized_query

def sql_injection_detector(query: str):
    # 检测 SQL 注入模式
    return query

def toxicity_scanner(query: str):
    # 检测有害内容
    return query

# 组装安全管道
security_pipeline = SecurityPipeline()
security_pipeline.add_filter(pii_anonymizer)
security_pipeline.add_filter(sql_injection_detector)
security_pipeline.add_filter(toxicity_scanner)