import numpy as np
from OpenGL import GL as gl

from ..core.resources import get_assets_dir

_SHADER_DIR = get_assets_dir() / "shaders"


class QuadRenderer:
    def __init__(self):
        self.program = 0
        self.vao = 0
        self.vbo = 0
        self.u_resolution = -1
        self.u_offset = -1
        self.u_page_pos = -1
        self.u_page_size = -1
        self.u_is_placeholder = -1
        self.u_color = -1
        self.u_radius = -1
        self.u_flip_v = -1
        self._offset_x = 0.0
        self._offset_y = 0.0

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
        self.u_radius = gl.glGetUniformLocation(self.program, "u_radius")
        self.u_flip_v = gl.glGetUniformLocation(self.program, "u_flip_v")

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
        self.vao = 0
        self.vbo = 0
        self.program = 0

    def begin(self, viewport_w: int, viewport_h: int, offset_x: float, offset_y: float, gl_scale: int):
        physical_w = int(viewport_w * gl_scale)
        physical_h = int(viewport_h * gl_scale)

        self._offset_x = float(offset_x)
        self._offset_y = float(offset_y)

        gl.glViewport(0, 0, physical_w, physical_h)
        gl.glClearColor(0.88, 0.88, 0.88, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glUseProgram(self.program)
        gl.glUniform2f(self.u_resolution, float(viewport_w), float(viewport_h))
        gl.glUniform2f(self.u_offset, float(round(offset_x)), float(round(offset_y)))
        gl.glUniform1f(self.u_flip_v, 0.0)

        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)

        gl.glBindVertexArray(self.vao)

    def white_card(self, x: float, y: float, w: float, h: float):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glUniform1i(self.u_is_placeholder, 1)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def fill_rect(self, x: float, y: float, w: float, h: float, color: tuple[float, float, float, float]):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glUniform1i(self.u_is_placeholder, 2)
        gl.glUniform4f(self.u_color, *color)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def fill_round_rect(self, x: float, y: float, w: float, h: float,
                        color: tuple[float, float, float, float], radius: float):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glUniform1i(self.u_is_placeholder, 3)
        gl.glUniform4f(self.u_color, *color)
        gl.glUniform1f(self.u_radius, float(radius))
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def composite_layer(self, tex_id: int, viewport_w: int, viewport_h: int):
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
        gl.glUniform1i(self.u_is_placeholder, 4)
        gl.glUniform1f(self.u_flip_v, 1.0)
        gl.glUniform2f(self.u_page_pos, self._offset_x, self._offset_y)
        gl.glUniform2f(self.u_page_size, float(viewport_w), float(viewport_h))
        gl.glBlendFunc(gl.GL_DST_COLOR, gl.GL_ZERO)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glUniform1f(self.u_flip_v, 0.0)

    def textured(self, tex_id: int, x: float, y: float, w: float, h: float):
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
        gl.glUniform1i(self.u_is_placeholder, 0)
        gl.glUniform2f(self.u_page_pos, float(x), float(y))
        gl.glUniform2f(self.u_page_size, float(w), float(h))
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

    def end(self):
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glBindVertexArray(0)


class CompositingLayer:
    """Offscreen RGBA framebuffers for multi-pass highlight rendering.

    Pass 1: Character boxes for each highlight are drawn into FBO 1 at opacity 1.0.
    Pass 2: Highlights in FBO 1 are composited into FBO 2 at alpha 0.5, converting
            transparent background pixels to white (1,1,1,1).
    Pass 3: FBO 2 texture is multiply-composited over the page frame on default_fbo.
    """

    def __init__(self):
        self.fbo1 = 0
        self.texture1 = 0
        self.fbo2 = 0
        self.texture2 = 0
        self.phys_w = 0
        self.phys_h = 0
        self._default_fbo = 0

    def _create_fbo_and_tex(self, phys_w: int, phys_h: int) -> tuple[int, int]:
        fbo = int(gl.glGenFramebuffers(1) or 0)
        tex = int(gl.glGenTextures(1) or 0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, phys_w, phys_h, 0,
                        gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, None)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, fbo)
        gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                                  gl.GL_TEXTURE_2D, tex, 0)
        status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        if status != gl.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"Compositing layer framebuffer incomplete: {status}")
        return fbo, tex

    def ensure_size(self, logical_w: float, logical_h: float, gl_scale: int) -> None:
        phys_w = max(1, int(logical_w * gl_scale))
        phys_h = max(1, int(logical_h * gl_scale))
        if self.texture1 and self.texture2 and self.phys_w == phys_w and self.phys_h == phys_h:
            return
        self.cleanup()
        self.phys_w = phys_w
        self.phys_h = phys_h
        self.fbo1, self.texture1 = self._create_fbo_and_tex(phys_w, phys_h)
        self.fbo2, self.texture2 = self._create_fbo_and_tex(phys_w, phys_h)

    def prepare_compositing_layer(self, default_fbo: int) -> None:
        self._default_fbo = default_fbo
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo2)
        gl.glViewport(0, 0, self.phys_w, self.phys_h)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

    def bind_accumulation(self, default_fbo: int) -> None:
        self._default_fbo = default_fbo
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo1)
        gl.glViewport(0, 0, self.phys_w, self.phys_h)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)

    def composite_highlight_to_layer2(self, renderer: QuadRenderer, viewport_w: float, viewport_h: float) -> None:
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo2)
        gl.glViewport(0, 0, self.phys_w, self.phys_h)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture1)
        gl.glUniform1i(renderer.u_is_placeholder, 4)
        gl.glUniform1f(renderer.u_flip_v, 1.0)
        gl.glUniform2f(renderer.u_page_pos, renderer._offset_x, renderer._offset_y)
        gl.glUniform2f(renderer.u_page_size, float(viewport_w), float(viewport_h))
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
        gl.glUniform1f(renderer.u_flip_v, 0.0)

    def composite_to_page(self, renderer: QuadRenderer, viewport_w: float, viewport_h: float, gl_scale: int) -> None:
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self._default_fbo)
        gl.glViewport(0, 0, int(viewport_w * gl_scale), int(viewport_h * gl_scale))

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture2)
        gl.glUniform1i(renderer.u_is_placeholder, 5)
        gl.glUniform1f(renderer.u_flip_v, 1.0)
        gl.glUniform2f(renderer.u_page_pos, renderer._offset_x, renderer._offset_y)
        gl.glUniform2f(renderer.u_page_size, float(viewport_w), float(viewport_h))
        gl.glBlendFunc(gl.GL_DST_COLOR, gl.GL_ZERO)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glUniform1f(renderer.u_flip_v, 0.0)

    def cleanup(self) -> None:
        if self.texture1:
            gl.glDeleteTextures([self.texture1])
            self.texture1 = 0
        if self.fbo1:
            gl.glDeleteFramebuffers(1, [self.fbo1])
            self.fbo1 = 0
        if self.texture2:
            gl.glDeleteTextures([self.texture2])
            self.texture2 = 0
        if self.fbo2:
            gl.glDeleteFramebuffers(1, [self.fbo2])
            self.fbo2 = 0
        self.phys_w = 0
        self.phys_h = 0
