"""静态审计 Qt 独立窗口、模态对话框与显式菜单来源；不运行 GUI、不联网。"""
from __future__ import annotations
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def audit(root=ROOT):
    records = []
    focus_calls = []
    embedded_native_sources = []
    class_bases = {'QDialog', 'QMainWindow', 'QMenu', 'QProgressDialog', 'QMessageBox'}
    dialog_calls = {'QDialog', 'QMessageBox', 'QProgressDialog', 'QMenu', 'QInputDialog', 'QFileDialog'}
    active_calls = {'activateWindow', 'raise_', 'setFocus', 'exec', 'focus_force', 'focus_set',
                    'grab_set', 'grab_set_global', 'SetForegroundWindow'}
    for path in sorted((root/'src').rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        relative = path.relative_to(root).as_posix()
        def scan(nodes, scope='module', owner=''):
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    name = f'{scope}.{node.name}' if scope!='module' else node.name
                    bases = [ast.unparse(base).split('.')[-1] for base in node.bases]
                    if any(base in class_bases for base in bases):
                        records.append(dict(file=relative, line=node.lineno, function=name+'.__init__', kind='dialog_class', receiver='self'))
                    scan(node.body, name, name)
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan(node.body, f'{scope}.{node.name}' if scope!='module' else node.name, owner)
                    continue
                if isinstance(node, ast.Call):
                    func = node.func
                    method = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ''
                    receiver = ast.unparse(func.value) if isinstance(func, ast.Attribute) else ''
                    if method in {'setWindowFlags','setWindowFlag'}:
                        records.append(dict(file=relative, line=node.lineno, function=scope, kind='window_flags', receiver=receiver))
                    elif method=='__init__' and isinstance(func,ast.Attribute) and isinstance(func.value,ast.Call) and isinstance(func.value.func,ast.Name) and func.value.func.id=='super' and (len(node.args)>1 or any(k.arg=='flags' for k in node.keywords)):
                        records.append(dict(file=relative,line=node.lineno,function=scope,kind='window_flags_constructor',receiver='self'))
                    elif method in dialog_calls or (receiver in dialog_calls and method in {'information','warning','critical','question','getText','getOpenFileName','getSaveFileName','getExistingDirectory'}):
                        records.append(dict(file=relative, line=node.lineno, function=scope, kind='dialog_or_menu_call', receiver=ast.unparse(func)))
                    if method in active_calls:
                        focus_calls.append(dict(file=relative, line=node.lineno, function=scope, call=ast.unparse(func)))
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and 'SetForegroundWindow' in node.value:
                    embedded_native_sources.append(dict(file=relative,line=node.lineno,function=scope,
                        kind='embedded_user32_script',apis=['SetForegroundWindow','ShowWindow','AttachThreadInput']))
                scan(list(ast.iter_child_nodes(node)), scope, owner)
        scan(tree.body)
    return {'method':'Static source sites; Qt child widgets are excluded unless window flags are configured. Class and explicit policy sites may describe the same runtime window.',
            'source_sites': records, 'focus_and_modal_calls': focus_calls, 'embedded_native_sources':embedded_native_sources}

if __name__=='__main__':
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
