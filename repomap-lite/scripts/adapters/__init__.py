"""
adapters/ — 各语言/技术栈的适配器集合。

新增一种语言支持的步骤：
  1. 在这个目录下新建一个文件，比如 rust_adapter.py
  2. 实现一个满足 adapter_base.LanguageAdapter 接口的类
     （match(filepath) -> bool，extract_symbols(filepath, lines) -> AdapterResult）
  3. 在文件末尾调用 register(YourAdapter()) 完成自注册
  4. 在本文件（__init__.py）的 import 列表里加一行 import，确保它被加载到

不需要改动 repomap_lite.py、adapter_base.py 或任何其他适配器文件。

注册顺序很重要：更"专用"/更容易跟通用规则冲突的适配器要排在前面。
例如 .vue 文件本身不会被 JS/TS 适配器的 match() 命中（扩展名不同），
所以目前的适配器之间没有扩展名冲突，顺序其实不敏感；但如果未来新增的适配器
在 match() 里用了更宽松的判断（比如按文件内容特征而不是扩展名），
就需要注意把更精确的判断放在前面。
"""

from adapters.python_adapter import PythonAdapter  # noqa: F401
from adapters.js_ts_adapter import JsTsAdapter  # noqa: F401
from adapters.go_adapter import GoAdapter  # noqa: F401
from adapters.c_family_adapter import CFamilyAdapter, DIALECTS  # noqa: F401
from adapters.rust_adapter import RustAdapter  # noqa: F401
from adapters.ruby_adapter import RubyAdapter  # noqa: F401
from adapters.makefile_adapter import MakefileAdapter  # noqa: F401
from adapters.dockerfile_adapter import DockerfileAdapter  # noqa: F401
from adapters.vue_adapter import VueAdapter  # noqa: F401
from adapters.shader_adapter import ShaderAdapter  # noqa: F401

# 每个模块在被 import 时，其模块级的 register(...) 调用已经执行，
# 全局注册表（adapter_base._REGISTRY）此时已经包含全部适配器。
# 这个文件本身不需要再做任何事，上面的 import 就是全部的注册触发点。
