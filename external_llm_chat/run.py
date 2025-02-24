import sys
from pathlib import Path
import os

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

import argparse
import dataclasses
import inspect
import logging
from flask import Flask, request, jsonify  # 新增Flask依赖
import torch
from utils import load_chat_template, sampling_add_cli_args
from vllm import LLM, EngineArgs, SamplingParams

app = Flask(__name__)  # 创建Flask应用

# 全局变量存储模型实例和配置
global_llm = None
global_tokenizer = None


def initialize_service(args):
    """初始化模型服务"""
    global global_llm, global_tokenizer

    # 解析引擎参数
    engine_args = [attr.name for attr in dataclasses.fields(EngineArgs)]
    engine_params = {attr: getattr(args, attr) for attr in engine_args}

    # 初始化模型
    global_llm = LLM(**engine_params)
    global_tokenizer = global_llm.get_tokenizer()
    load_chat_template(global_tokenizer, args.chat_template)

    logging.info("Service initialization completed")


@app.route('/chat', methods=['POST'])
def chat_endpoint():
    """对话处理端点"""
    global global_llm, global_tokenizer

    # 解析请求数据
    data = request.get_json()
    if not data or 'messages' not in data:
        return jsonify({"error": "Invalid request format"}), 400

    try:
        # 处理对话历史
        messages = data['messages']

        # 构建提示
        prompt_text = global_tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )

        # 生成回复
        sampling_params = SamplingParams(max_tokens=1024)
        outputs = global_llm.generate(
            [prompt_text],
            sampling_params,
        )

        generated_texts = []

        for output in outputs:
            generated_texts.append(output.outputs[0].text)

        return generated_texts

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # 参数解析
    parser = argparse.ArgumentParser(description='LLM API Service')
    parser.add_argument("--chat_template", type=str, default=None)
    parser.add_argument(
        "--remove_chat_template",
        action="store_true",
        help="Disable chat template processing"
    )
    parser = EngineArgs.add_cli_args(parser)
    parser = sampling_add_cli_args(parser)

    # 添加服务专用参数
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    # 初始化服务
    initialize_service(args)

    # 启动服务
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=False  # 必须关闭reloader以保证单进程
    )
