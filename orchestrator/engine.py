# 占位：将 workflows/*.yaml 编译为 DAG
def compile_workflow(yaml_path: str) -> dict:
    return {'workflow': yaml_path, 'compiled': True}
