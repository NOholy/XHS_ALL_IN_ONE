import sys
import logging

logging.basicConfig(level=logging.INFO)

try:
    from mobile_core.pipeline.loader import PipelineLoader
    loader = PipelineLoader(strict=False)
    pipeline = loader.load("config/pipelines/farm_session.yaml")
    print(f"Pipeline '{pipeline.name}' loaded successfully. Nodes: {len(pipeline.nodes)}")
    
    import importlib
    def _import_handler(handler_path):
        module_path, func_name = handler_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, func_name)

    # Try importing custom handlers
    for node in pipeline.nodes.values():
        if node.recognition.type.value == "custom" and node.recognition.handler:
            print(f"Validating recognition handler: {node.recognition.handler}")
            _import_handler(node.recognition.handler)
            
        if node.action.type.value == "custom" and node.action.handler:
            print(f"Validating action handler: {node.action.handler}")
            _import_handler(node.action.handler)
            
    print("All custom handlers resolved successfully!")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
