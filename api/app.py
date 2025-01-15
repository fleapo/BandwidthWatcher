from flask import Flask
import os
app = Flask(__name__)

@app.route("/")
def hello():
  # 打印环境变量的keys
  env_names = (os.environ.keys())
  env_names_str = "\n".join(env_names)
  return env_names_str