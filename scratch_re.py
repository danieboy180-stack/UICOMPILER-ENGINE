import re

text = """
<LAYER1_OUTPUT>
{"a": 1}
</LAYER1_OUTPUT>
<LAYER2_OUTPUT>
{"b": 2}
</LAYER2_OUTPUT>
"""
l1 = re.search(r'<LAYER1_OUTPUT>(.*?)</LAYER1_OUTPUT>', text, re.DOTALL)
print(l1.group(1).strip() if l1 else None)
