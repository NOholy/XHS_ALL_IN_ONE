"""
Pipeline 可视化报告生成器 (HTML)

生成类似 Airtest 风格的执行报告。
记录时间线、命中节点、截图和成功率。
"""

import os
import json
import base64
from datetime import datetime
import cv2

from mobile_core.logger import get_logger

logger = get_logger("pipeline.reporter")

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Pipeline Execution Report</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; color: #333; }
        .header { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .header h1 { margin: 0 0 10px 0; color: #2c3e50; }
        .summary { display: flex; gap: 20px; flex-wrap: wrap; }
        .stat-card { background: #e8f4f8; padding: 15px; border-radius: 8px; flex: 1; min-width: 150px; text-align: center; }
        .stat-card h3 { margin: 0 0 5px 0; font-size: 14px; color: #555; }
        .stat-card .value { font-size: 24px; font-weight: bold; color: #2980b9; }
        
        .timeline { display: flex; flex-direction: column; gap: 15px; }
        .step { background: #fff; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: flex; flex-direction: row; gap: 20px; }
        .step.error { border-left: 5px solid #e74c3c; }
        .step.success { border-left: 5px solid #2ecc71; }
        
        .step-info { flex: 1; }
        .step-img { flex: 0 0 300px; display: flex; align-items: center; justify-content: center; background: #eee; border-radius: 4px; overflow: hidden; }
        .step-img img { max-width: 100%; max-height: 400px; object-fit: contain; }
        
        .node-name { font-size: 18px; font-weight: bold; margin-bottom: 10px; color: #2c3e50; }
        .meta { font-size: 13px; color: #7f8c8d; margin-bottom: 5px; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: #fff; }
        .badge.success { background: #2ecc71; }
        .badge.failed { background: #e74c3c; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Pipeline Report: {pipeline_name}</h1>
        <div class="summary">
            <div class="stat-card"><h3>Duration</h3><div class="value">{duration}s</div></div>
            <div class="stat-card"><h3>Nodes Hit</h3><div class="value">{nodes_hit}</div></div>
            <div class="stat-card"><h3>Actions Executed</h3><div class="value">{actions}</div></div>
            <div class="stat-card"><h3>Errors</h3><div class="value" style="color: {error_color}">{errors}</div></div>
            <div class="stat-card"><h3>Screenshots</h3><div class="value">{screenshots}</div></div>
        </div>
    </div>
    
    <h2>Execution Timeline</h2>
    <div class="timeline">
        {timeline_html}
    </div>
</body>
</html>
"""

STEP_TEMPLATE = """
<div class="step {status_class}">
    <div class="step-info">
        <div class="node-name">{index}. {node_name}</div>
        <div class="meta"><strong>Time:</strong> {time}</div>
        <div class="meta"><strong>Confidence:</strong> {confidence:.2f}</div>
        <div class="meta"><strong>Position:</strong> {position}</div>
        <div class="meta"><strong>Action Status:</strong> <span class="badge {status_class}">{action_status}</span></div>
    </div>
    <div class="step-img">
        <img src="data:image/jpeg;base64,{img_b64}" alt="Screenshot">
    </div>
</div>
"""

class HtmlReporter:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
    def generate(self, pipeline_name: str, stats):
        logger.info(f"Generating HTML report for {pipeline_name}...")
        
        timeline_html = []
        for i, item in enumerate(stats.timeline):
            action_success = item.get("action_success", True)
            status_class = "success" if action_success else "error"
            action_status = "SUCCESS" if action_success else "FAILED"
            
            # Encode image
            img_b64 = ""
            if item.get("screen") is not None:
                # Resize image for report to save space
                h, w = item["screen"].shape[:2]
                scale = min(1.0, 800 / max(h, w))
                if scale < 1.0:
                    img = cv2.resize(item["screen"], (int(w*scale), int(h*scale)))
                else:
                    img = item["screen"]
                    
                _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 60])
                img_b64 = base64.b64encode(buf).decode('utf-8')
                
            time_str = datetime.fromtimestamp(item["timestamp"]).strftime('%H:%M:%S.%f')[:-3]
            
            step_html = STEP_TEMPLATE.format(
                status_class=status_class,
                index=i+1,
                node_name=item["node_name"],
                time=time_str,
                confidence=item.get("reco_confidence", 0.0),
                position=item.get("reco_position", "None"),
                action_status=action_status,
                img_b64=img_b64
            )
            timeline_html.append(step_html)
            
        summary = stats.summary()
        html = HTML_TEMPLATE.format(
            pipeline_name=pipeline_name,
            duration=summary["duration_seconds"],
            nodes_hit=summary["nodes_hit"],
            actions=summary["actions_executed"],
            errors=summary["errors"],
            error_color="#e74c3c" if summary["errors"] > 0 else "#2ecc71",
            screenshots=summary["screenshots"],
            timeline_html="\\n".join(timeline_html)
        )
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = os.path.join(self.output_dir, f"report_{pipeline_name}_{timestamp}.html")
        
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(html)
            
        logger.info(f"HTML report saved to: {report_file}")
        return report_file
