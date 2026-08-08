import numpy as np
from OpenGL import GL as gl

from ..core.resources import get_assets_dir
from ..core.texture import PageTexture

_SHADER_DIR = get_assets_dir() / "shaders"


class QuadRenderer:
    def __init__(self):
        self.program = 0
        self.vao = 0
        self.vbo = 0
        self.textures: dict = {}
        self.u_resolution = -1
        self.u_offset = -1
        self.u_page_pos = -1
        self.u_page_size = -1
        self.u_is_placeholder = -1
        self.u_color = -1

    @staticmethod
    def _compile_shader(shader_type, source: str) -> int:
        shader = gl.glCreateShader(shader_type)
        gl.glShaderSource(shader, source)
        gl.glCompileShader(shader)
        if not gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS):
            info_log = gl.glGetShaderInfoLog(shader)
            raise RuntimeError(f"Shader compilation failed:\n{info_log.decode()}")
        return int(shader or 0)

    @staticmethod
    def _link_program(vs: int, fs: int) -> int:
        program = gl.glCreateProgram()
        gl.glAttachShader(program, vs)
        gl.glAttachShader(program, fs)
        gl.glLinkProgram(program)
        if not gl.glGetProgramiv(program, gl.GL_LINK_STATUS):
            info_log = gl.glGetProgramInfoLog(program)
            raise RuntimeError(f"Program linking failed:\n{info_log.decode()}")
        return int(program or 0)

    def initialize(self):
        vs = self._compile_shader(gl.GL_VERTEX_SHADER,
                                  (_SHADER_DIR / "quad.vert").read_text())
        fs = self._compile_shader(gl.GL_FRAGMENT_SHADER,
                                  (_SHADER_DIR / "quad.frag").read_text())

        self.program = gl.glCreateProgram()
        gl.glAttachShader(self.program, vs)
        gl.glAttachShader(self.program, fs)
        gl.glLinkProgram(self.program)

        if gl.glGetProgramiv(self.program, gl.GL_LINK_STATUS) != gl.GL_TRUE:
            info = gl.glGetProgramInfoLog(self.program)
            raise RuntimeError(f"Shader linking failed: {info}")

        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)

        self.u_resolution = gl.glGetUniformLocation(self.program, "u_resolution")
        self.u_offset = gl.glGetUniformLocation(self.program, "u_offset")
        self.u_page_pos = gl.glGetUniformLocation(self.program, "u_page_pos")
        self.u_page_size = gl.glGetUniformLocation(self.program, "u_page_size")
        self.u_is_placeholder = gl.glGetUniformLocation(self.program, "u_is_placeholder")
        self.u_color = gl.glGetUniformLocation(self.program, "u_color")

        vertices = np.array([
            0.0, 0.0, 0.0, 0.0,
            1.0, 0.0, 1.0, 0.0,
            0.0, 1.0, 0.0, 1.0,
            0.0, 1.0, 0.0, 1.0,
            1.0, 0.0, 1.0, 0.0,
            1.0, 1.0, 1.0, 1.0,
        ], dtype=np.float32)

        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)

        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, vertices.nbytes, vertices, gl.GL_STATIC_DRAW)

        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, gl.ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * 4, gl.ctypes.c_void_p(2 * 4))
        gl.glEnableVertexAttribArray(1)

        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glBindVertexArray(0)
        print("[GLCanvas] OpenGL pipeline initialized successfully.")

    def cleanup(self):
        if self.vao:
            gl.glDeleteVertexArrays(1, [self.vao])
        if self.vbo:
            gl.glDeleteBuffers(1, [self.vbo])
        if self.program:
            gl.glDeleteProgram(self.program)
        for tex_id in self.textures.values():
            gl.glDeleteTextures(1, [tex_id])
        self.textures.clear()

    def begin(self, viewport_w: int, viewport_h: int, offset_x: float, offset_y: float, gl_scale: int):
        physical_w = int(viewport_w * gl_scale)
        physical_h = int(viewport_h * gl_scale)

        gl.glViewport(0, 0, physical_w, physical_h)
        gl.glClearColor(0.88, 0.88, 0.88, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glUseProgram(self.program)
        gl.glUniform2f(self.u_resolution, float(viewport_w), float(viewport_h))
        gl.glUniform2f(self.u_offset, float(round(offset_x)), float(round(offset_y)))

        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)

        gl.glBindVertexArray(self.vao)

    def upload_surface(self, texture: PageTexture) -> int:
        tex_id = self.textures.get(texture)
        if tex_id is not None:
            return tex_id

        w = texture.width
        h = texture.height

        tex_id = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)

        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)

        gl.glTexImage2D(
            gl.GL_TEXTURE_2D, 0, gl.GL_RGB8, w, h, 0,
            gl.GL_RGB, gl.GL_UNSIGNED_BYTE, texture.samples,
        )
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

        self.textures[texture] = tex_id
        return tex_id

    def white_card(self, x: float, y: float, w: float, h: float):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glUniform1i(self.u_is_placeholder, 1)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def fill_rect(self, x: float, y: float, w: float, h: float, color: tuple[float, float, float, float], mode: str = "alpha"):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glUniform1i(self.u_is_placeholder, 2)
        gl.glUniform4f(self.u_color, *color)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        if mode == "multiply":
            gl.glBlendFunc(gl.GL_DST_COLOR, gl.GL_ZERO)
        else:
            gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
        if mode == "multiply":
            gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)

    def textured(self, tex_id: int, x: float, y: float, w: float, h: float):
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
        gl.glUniform1i(self.u_is_placeholder, 0)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def end(self):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glBindVertexArray(0)
