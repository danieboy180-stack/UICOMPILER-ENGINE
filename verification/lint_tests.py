import sys
import ast
import re
from pathlib import Path

def check_file(path: Path) -> list:
    errors = []
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as e:
        return [f"Could not read file: {e}"]
        
    # Check for ignore comments near pass/success
    if re.search(r'(?i)#.*(ignore|skip for now|TODO:\s*fix).*\n\s*(pass|assert True|return True|record_verification)', content):
        errors.append("Ignore/skip comment adjacent to pass/success branch")
        
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        errors.append(f"SyntaxError: {e}")
        return errors
        
    class LintVisitor(ast.NodeVisitor):
        def __init__(self):
            self.errors = []
            self.in_test_func = False
            
        def visit_ExceptHandler(self, node):
            # Check for except: pass or except Exception: pass
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                if node.type is None or (isinstance(node.type, ast.Name) and node.type.id == 'Exception'):
                    self.errors.append(f"Line {node.lineno}: Silently swallowed error (except: pass)")
            self.generic_visit(node)
            
        def visit_If(self, node):
            # Check for if X or True: or if X or fallback:
            # We can just look at the test expression
            if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or):
                for val in node.test.values:
                    if isinstance(val, ast.Constant) and val.value is True:
                        self.errors.append(f"Line {node.lineno}: Fallback condition (or True)")
                    if isinstance(val, ast.Name) and val.id == 'fallback':
                        self.errors.append(f"Line {node.lineno}: Fallback condition (or fallback)")
            self.generic_visit(node)
            
        def visit_FunctionDef(self, node):
            if node.name.startswith("test_"):
                has_assert = False
                has_record = False
                for child in ast.walk(node):
                    if isinstance(child, ast.Assert):
                        has_assert = True
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == 'record_verification':
                            has_record = True
                        if isinstance(child.func, ast.Attribute) and child.func.attr == 'record_verification':
                            has_record = True
                
                if not has_assert and not has_record:
                    # we will flag this function
                    self.errors.append(f"Line {node.lineno}: Function {node.name} has zero asserts and zero calls to record_verification")
            self.generic_visit(node)

    visitor = LintVisitor()
    visitor.visit(tree)
    errors.extend(visitor.errors)
                
    return errors

def main():
    root = Path.cwd()
    test_files = list(root.glob("test_*.py"))
    
    failed = False
    for tf in test_files:
        errs = check_file(tf)
        if errs:
            failed = True
            print(f"FAIL: {tf.name}")
            for e in errs:
                print(f"  - {e}")
                
    if failed:
        sys.exit(1)
    else:
        print("LINT PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
