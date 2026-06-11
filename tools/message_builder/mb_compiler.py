
import ast
import logging
from typing import Any, Dict, List, Tuple, Union

logger = logging.getLogger("MBCompiler")

class ASTValidationError(Exception):
    pass

class LayoutASTVisitor(ast.NodeVisitor):
    def __init__(self):
        self.whitelisted_calls = {
            "container", "section", "textdisplay", "separator", "button", 
            "userselect", "roleselect", "channelselect", "mentionableselect", 
            "stringselect", "selectoption", "actionrow", "label", "checkbox", 
            "checkboxgroup", "radiogroup", "fileupload", "modal", "action",
            "textinput",
            "trigger_ai", "trigger_image_generation", "reply_private", "reply_public", "delete_message", 
            "disable_components", "pass_input", "open_modal"
        }
        self.has_modal_action = False
        self.in_modal_declaration = False

    def visit_Import(self, node):
        raise ASTValidationError("Imports are strictly forbidden inside layout scripts.")

    def visit_ImportFrom(self, node):
        raise ASTValidationError("Imports are strictly forbidden inside layout scripts.")

    def visit_FunctionDef(self, node):
        raise ASTValidationError("Defining functions is forbidden. Write declarative component constructors only.")

    def visit_For(self, node):
        raise ASTValidationError("Control loops are forbidden inside layout scripts.")

    def visit_While(self, node):
        raise ASTValidationError("Control loops are forbidden inside layout scripts.")

    def visit_Call(self, node):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "Action":
                func_name = f"Action.{node.func.attr}"
            else:
                func_name = f"{node.func.value.id}.{node.func.attr}"

        base_func = func_name.split(".")[-1].lower().strip()
        if base_func not in self.whitelisted_calls:
            raise ASTValidationError(f"Call to unapproved component or function: '{func_name}'")

        if base_func == "open_modal":
            self.has_modal_action = True
            if self.in_modal_declaration:
                raise ASTValidationError(
                    "No Chained Modals Constraint: You are strictly forbidden from launching "
                    "a Modal from within a Modal submission callback."
                )

        if base_func == "modal":
            previous_modal_state = self.in_modal_declaration
            self.in_modal_declaration = True
            self.generic_visit(node)
            self.in_modal_declaration = previous_modal_state
        else:
            self.generic_visit(node)


def parse_action_arg(node: ast.AST) -> Union[str, Dict[str, Any], List[Any]]:
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    elif isinstance(node, ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            func_name = f"{node.value.id}.{node.func.attr}"

        args = [parse_action_arg(arg) for arg in node.args]
        kwargs = {kw.arg: parse_action_arg(kw.value) for kw in node.keywords}
        
        return {"type": func_name, "args": args, "kwargs": kwargs}
    elif isinstance(node, ast.List):
        return [parse_action_arg(el) for el in node.elts]
    return ""


class ComponentASTBuilder:
    
    def evaluate_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.List):
            return [self.evaluate_node(el) for el in node.elts]
        elif isinstance(node, ast.Call):
            return self.build_call_element(node)
        elif isinstance(node, ast.Assign):
            return self.evaluate_node(node.value)
        elif isinstance(node, ast.Expr):
            return self.evaluate_node(node.value)
        return None

    def build_call_element(self, node: ast.Call) -> Dict[str, Any]:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            func_name = f"{node.func.value.id}.{node.func.attr}"

        args = [self.evaluate_node(arg) for arg in node.args]
        kwargs = {}
        for kw in node.keywords:
            if kw.arg:
                kwargs[kw.arg] = self.evaluate_node(kw.value)

        return {
            "type": func_name,
            "args": args,
            "kwargs": kwargs
        }


def compile_dsl_payload(dsl_code: str) -> Dict[str, Any]:
    clean_code = dsl_code.strip()
    if clean_code.startswith("```python"):
        clean_code = clean_code[9:]
    if clean_code.startswith("```json"):
        clean_code = clean_code[7:]
    if clean_code.endswith("```"):
        clean_code = clean_code[:-3]
    clean_code = clean_code.strip()

    try:
        tree = ast.parse(clean_code)
    except SyntaxError as syntax_err:
        raise ASTValidationError(f"Syntax Error in Python compilation: {syntax_err.msg} at line {syntax_err.lineno}")

    visitor = LayoutASTVisitor()
    visitor.visit(tree)

    builder = ComponentASTBuilder()
    
    compiled_layout = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
            
        stmt_val = builder.evaluate_node(stmt)
        if isinstance(stmt_val, dict) and "type" in stmt_val:
            compiled_layout = stmt_val
            break

    if not compiled_layout:
        raise ASTValidationError("Layout compilation failed. No root class assignment or layout instantiation found.")

    verify_structural_constraints(compiled_layout)
    
    def count_action_rows(node: Dict[str, Any]) -> int:
        if not isinstance(node, dict):
            return 0
        comp_name = node.get("type", "").split(".")[-1]
        total = 1 if comp_name == "ActionRow" else 0
        
        children_lists = []
        if isinstance(node.get("args"), list):
            children_lists.append(node["args"])
        if "children" in node.get("kwargs", {}):
            if isinstance(node["kwargs"]["children"], list):
                children_lists.append(node["kwargs"]["children"])
                
        for children in children_lists:
            for child in children:
                if isinstance(child, dict):
                    total += count_action_rows(child)
        return total

    total_rows = count_action_rows(compiled_layout)
    if total_rows > 5:
        raise ASTValidationError(
            f"Discord API Limit: A message cannot contain more than 5 ActionRow components (found {total_rows})."
        )
    return compiled_layout


def verify_structural_constraints(node: Dict[str, Any]):
    comp_type = node.get("type", "").split(".")[-1]
    kwargs = node.get("kwargs", {})
    
    if comp_type == "Section":
        if "accessory" not in kwargs:
            raise ASTValidationError(
                "Discord API Limit: A Section component must contain a valid 'accessory' (such as a Button). "
                "If you only want to display text without an accessory, use a standard Container(TextDisplay(...)) instead of a Section!"
            )

    if comp_type == "ActionRow":
        children = node.get("args", [])
        if not children and "children" in kwargs:
            children = node["kwargs"]["children"]
            
        dropdowns_count = 0
        buttons_count = 0
        
        for child in children:
            if not isinstance(child, dict):
                continue
            child_type = child.get("type", "").split(".")[-1]
            if child_type in ("UserSelect", "RoleSelect", "ChannelSelect", "MentionableSelect", "StringSelect"):
                dropdowns_count += 1
            elif child_type == "Button":
                buttons_count += 1
                
        if dropdowns_count > 1:
            raise ASTValidationError("Discord API Limit: An ActionRow cannot hold more than exactly 1 Select Dropdown menu.")
        if dropdowns_count > 0 and buttons_count > 0:
            raise ASTValidationError("Discord API Limit: You cannot mix Buttons and Select Dropdowns in the same ActionRow.")
        if buttons_count > 5:
            raise ASTValidationError("Discord API Limit: An ActionRow cannot hold more than 5 Button components.")
        if buttons_count == 0 and dropdowns_count == 0:
            raise ASTValidationError("Discord API Limit: An ActionRow must contain between 1 and 5 components.")

    for callback_prop in ("on_click", "on_select", "on_submit"):
        if callback_prop in kwargs:
            cb_val = kwargs[callback_prop]
            if isinstance(cb_val, list):
                has_modal = any(
                    isinstance(a, dict) and a.get("type", "").split(".")[-1].lower().strip() in ("open_modal", "openmodal", "modal") 
                    for a in cb_val
                )
                if has_modal:
                    if len(cb_val) > 1:
                        raise ASTValidationError(
                            "Discord Validation Exception: Action.open_modal() cannot be chained or "
                            "combined with other response operations in a multi-action callback list."
                        )

    children_lists = []
    if isinstance(node.get("args"), list):
        children_lists.append(node["args"])
    if "children" in kwargs:
        if isinstance(kwargs["children"], list):
            children_lists.append(kwargs["children"])
        elif isinstance(kwargs["children"], dict):
            children_lists.append([kwargs["children"]])
        
    for children in children_lists:
        for child in children:
            if isinstance(child, dict):
                verify_structural_constraints(child)