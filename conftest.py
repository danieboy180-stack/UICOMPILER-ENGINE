import ast
import inspect
import pytest

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.passed:
        try:
            source = inspect.getsource(item.obj)
            # Dedent source to avoid ast.parse IndentationError if it's a method
            import textwrap
            source = textwrap.dedent(source)
            tree = ast.parse(source)
            has_assert = False
            has_record = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert):
                    has_assert = True
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "record_verification":
                        has_record = True
                    elif isinstance(node.func, ast.Attribute) and node.func.attr == "record_verification":
                        has_record = True
            
            if not has_assert and not has_record:
                report.outcome = "failed"
                report.longrepr = "Test passed with no verifiable assertion — this is not allowed."
        except Exception as e:
            # If we can't parse it, fail safe
            report.outcome = "failed"
            report.longrepr = f"Could not verify test assertions due to parsing error: {e}"
