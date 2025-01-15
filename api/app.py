from flask import Flask
import os
app = Flask(__name__)

@app.route("/")
def hello():
  # 打印环境变量的keys
  print(os.environ.keys())
  return "Hello, World!"