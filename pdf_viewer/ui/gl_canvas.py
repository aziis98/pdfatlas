import cairo
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib

try:
    from OpenGL import GL as gl
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

class GLCanvas(Gtk.GLArea):
    """
    Hardware-accelerated OpenGL background rendering canvas.
    Renders visible pages from the RenderCache as GPU textures behind the transparent Gtk.ScrolledWindow.
    """
    def __init__(self, canvas_layout_provider):
        super().__init__()
        self.layout_provider = canvas_layout_provider # PDFCanvas instance
        
        self.set_required_version(3, 3)
        self.set_has_depth_buffer(False)
        self.set_has_stencil_buffer(False)
        
        self.shader_program = 0
        self.vao = 0
        self.vbo = 0
        
        # Texture cache: cairo.ImageSurface -> OpenGL texture ID
        self.textures = {}
        
        # Shader Uniform Locations
        self.u_resolution = -1
        self.u_offset = -1
        self.u_page_pos = -1
        self.u_page_size = -1
        self.u_color = -1
        
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)

    def _on_realize(self, area):
        if not HAS_OPENGL:
            self.set_error(GLib.Error.new_literal(GLib.quark_from_string("opengl"), 0, "PyOpenGL not installed"))
            return
            
        self.make_current()
        err = self.get_error()
        if err is not None:
            print(f"[GLCanvas] Context realization error: {err.message}")
            return

        # 1. Compile Shaders
        vertex_shader_source = """
        #version 330 core
        layout (location = 0) in vec2 aPos;
        layout (location = 1) in vec2 aTexCoord;
        
        out vec2 TexCoord;
        
        uniform vec2 u_resolution;
        uniform vec2 u_offset;
        uniform vec2 u_page_pos;
        uniform vec2 u_page_size;
        
        void main() {
            // Coordinate mapping: page offset -> viewport -> NDC
            vec2 pixel_pos = u_page_pos + aPos * u_page_size - u_offset;
            vec2 ndc_pos;
            ndc_pos.x = (pixel_pos.x / u_resolution.x) * 2.0 - 1.0;
            ndc_pos.y = 1.0 - (pixel_pos.y / u_resolution.y) * 2.0; // Flip Y for top-down coordinate space
            
            gl_Position = vec4(ndc_pos, 0.0, 1.0);
            TexCoord = aTexCoord;
        }
        """

        fragment_shader_source = """
        #version 330 core
        in vec2 TexCoord;
        out vec4 FragColor;
        
        uniform sampler2D u_texture;
        uniform int u_is_placeholder;
        uniform vec4 u_color;
        
        void main() {
            if (u_is_placeholder == 1) {
                // White page background placeholder
                FragColor = vec4(1.0, 1.0, 1.0, 1.0);
            } else if (u_is_placeholder == 2) {
                // Solid vector color (e.g. highlight fill / border)
                FragColor = u_color;
            } else {
                vec4 tex = texture(u_texture, TexCoord);
                // Cairo ARGB32 is stored as BGRA in memory. Swap R & B channels.
                FragColor = vec4(tex.b, tex.g, tex.r, tex.a);
            }
        }
        """

        try:
            vs = self._compile_shader(gl.GL_VERTEX_SHADER, vertex_shader_source)
            fs = self._compile_shader(gl.GL_FRAGMENT_SHADER, fragment_shader_source)
            
            self.shader_program = gl.glCreateProgram()
            gl.glAttachShader(self.shader_program, vs)
            gl.glAttachShader(self.shader_program, fs)
            gl.glLinkProgram(self.shader_program)
            
            if gl.glGetProgramiv(self.shader_program, gl.GL_LINK_STATUS) != gl.GL_TRUE:
                info = gl.glGetProgramInfoLog(self.shader_program)
                raise RuntimeError(f"Shader linking failed: {info}")
                
            gl.glDeleteShader(vs)
            gl.glDeleteShader(fs)
            
            # Get uniform locations
            self.u_resolution = gl.glGetUniformLocation(self.shader_program, "u_resolution")
            self.u_offset = gl.glGetUniformLocation(self.shader_program, "u_offset")
            self.u_page_pos = gl.glGetUniformLocation(self.shader_program, "u_page_pos")
            self.u_page_size = gl.glGetUniformLocation(self.shader_program, "u_page_size")
            self.u_is_placeholder = gl.glGetUniformLocation(self.shader_program, "u_is_placeholder")
            self.u_color = gl.glGetUniformLocation(self.shader_program, "u_color")
            
        except Exception as e:
            print(f"[GLCanvas] Shader compiler error: {e}")
            self.set_error(GLib.Error.new_literal(GLib.quark_from_string("opengl"), 1, str(e)))
            return

        # 2. Setup unit quad VAO/VBO [0, 1] range
        vertices = [
            # pos_x, pos_y,  tex_u, tex_v
            0.0, 0.0,        0.0, 0.0,
            1.0, 0.0,        1.0, 0.0,
            0.0, 1.0,        0.0, 1.0,
            
            0.0, 1.0,        0.0, 1.0,
            1.0, 0.0,        1.0, 0.0,
            1.0, 1.0,        1.0, 1.0
        ]
        
        import ctypes
        vertex_data = (ctypes.c_float * len(vertices))(*vertices)
        
        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)
        
        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, ctypes.sizeof(vertex_data), vertex_data, gl.GL_STATIC_DRAW)
        
        # Position attribute (x, y)
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * ctypes.sizeof(ctypes.c_float), ctypes.c_void_p(0))
        
        # Texture coordinates attribute (u, v)
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(1, 2, gl.GL_FLOAT, gl.GL_FALSE, 4 * ctypes.sizeof(ctypes.c_float), ctypes.c_void_p(2 * ctypes.sizeof(ctypes.c_float)))
        
        gl.glBindVertexArray(0)
        print("[GLCanvas] OpenGL pipeline initialized successfully.")

    def _compile_shader(self, shader_type, source):
        shader = gl.glCreateShader(shader_type)
        gl.glShaderSource(shader, source)
        gl.glCompileShader(shader)
        if gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS) != gl.GL_TRUE:
            info = gl.glGetShaderInfoLog(shader)
            raise RuntimeError(f"Shader compilation failed: {info}")
        return shader

    def _on_unrealize(self, area):
        self.make_current()
        if self.shader_program:
            gl.glDeleteProgram(self.shader_program)
        if self.vao:
            gl.glDeleteVertexArrays(1, [self.vao])
        if self.vbo:
            gl.glDeleteBuffers(1, [self.vbo])
            
        # Clean up OpenGL textures
        for tex_id in self.textures.values():
            gl.glDeleteTextures([tex_id])
        self.textures.clear()

    def _on_render(self, area, context):
        if not HAS_OPENGL or self.shader_program == 0:
            return False

        # Query layout and viewport parameters from parent scrolled window
        canvas = self.layout_provider
        if not canvas or not canvas.doc_model or not canvas.vadjustment:
            # Clear screen to default background color
            gl.glClearColor(0.88, 0.88, 0.88, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            return True

        y_min = canvas.vadjustment.get_value()
        page_size = canvas.vadjustment.get_page_size()
        y_max = y_min + page_size
        
        scale_factor = canvas.get_scale_factor()
        viewport_w = self.get_allocated_width()
        viewport_h = self.get_allocated_height()

        # Get physical scale factor for the GL viewport
        gl_scale = self.get_scale_factor()
        physical_w = int(viewport_w * gl_scale)
        physical_h = int(viewport_h * gl_scale)

        # Set viewport to physical dimensions and clear screen
        gl.glViewport(0, 0, physical_w, physical_h)
        gl.glClearColor(0.88, 0.88, 0.88, 1.0) # Matches workspace gray (#e0e0e0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glUseProgram(self.shader_program)
        gl.glUniform2f(self.u_resolution, float(viewport_w), float(viewport_h))
        gl.glUniform2f(self.u_offset, 0.0, float(y_min))

        # Enable Alpha Blending for clean page anti-aliasing
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA) # Premultiplied alpha support

        gl.glBindVertexArray(self.vao)

        # Build list of active surfaces currently in the RenderCache
        active_surfaces = set()
        
        # Determine which pages are visible in the viewport
        page_count = len(canvas.page_layout)
        for i in range(page_count):
            y_offset, dw, dh, crop_rect = canvas.page_layout[i]
            # Shift Y positions by canvas top padding (page_gap size)
            page_y0 = y_offset + canvas.page_gap
            page_y1 = page_y0 + dh
            
            if page_y1 >= y_min and page_y0 <= y_max:
                # Center page horizontally inside viewport
                x_offset = max(0.0, (viewport_w - dw) / 2)
                
                # Draw white page background card
                gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                gl.glUniform1i(self.u_is_placeholder, 1)
                gl.glUniform2f(self.u_page_pos, float(x_offset), float(page_y0))
                gl.glUniform2f(self.u_page_size, float(dw), float(dh))
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                
                # Fetch cached Cairo surface
                surface = canvas.cache.get(i, canvas.zoom, scale_factor, crop_rect)
                
                if surface is not None:
                    active_surfaces.add(surface)
                    tex_id = self.textures.get(surface)
                    
                    if tex_id is None:
                        # Upload Cairo image surface raw data to GPU texture
                        w = surface.get_width()
                        h = surface.get_height()
                        data = surface.get_data() # memoryview of raw BGRA bytes
                        
                        tex_id = gl.glGenTextures(1)
                        gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
                        
                        # Upload bytes as GL_RGBA (premultiplied BGRA layout mapped via shader)
                        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, w, h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data.tobytes())
                        
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                        
                        self.textures[surface] = tex_id
                        
                    gl.glBindTexture(gl.GL_TEXTURE_2D, tex_id)
                    gl.glUniform1i(self.u_is_placeholder, 0)
                    
                    # Draw page textured quad
                    gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                    
                    # Draw block highlights if selected
                    if canvas.highlighted_block is not None:
                        h_page_idx, h_bbox = canvas.highlighted_block
                        if h_page_idx == i:
                            bx0, by0, bx1, by1 = h_bbox
                            crop_off_x = crop_rect.x0 if crop_rect is not None else 0.0
                            crop_off_y = crop_rect.y0 if crop_rect is not None else 0.0
                            
                            # Highlight bounds relative to page top-left
                            hx = x_offset + (bx0 - crop_off_x) * canvas.zoom * canvas.dpi_scale_factor
                            hy = page_y0 + (by0 - crop_off_y) * canvas.zoom * canvas.dpi_scale_factor
                            hw = (bx1 - bx0) * canvas.zoom * canvas.dpi_scale_factor
                            hh = (by1 - by0) * canvas.zoom * canvas.dpi_scale_factor
                            
                            # Bind null texture for solid colors
                            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                            gl.glUniform1i(self.u_is_placeholder, 2)
                            
                            # 1. Draw yellow fill quad (premultiplied: yellow is [1, 0.85, 0, 1], multiplied by alpha 0.3)
                            gl.glUniform4f(self.u_color, 0.3, 0.255, 0.0, 0.3)
                            gl.glUniform2f(self.u_page_pos, float(hx), float(hy))
                            gl.glUniform2f(self.u_page_size, float(hw), float(hh))
                            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                            
                            # 2. Draw red border edges (thin 2px quads)
                            gl.glUniform4f(self.u_color, 0.85, 0.1, 0.1, 0.9) # red border
                            border_t = 2.0
                            
                            # Top Edge
                            gl.glUniform2f(self.u_page_pos, float(hx), float(hy))
                            gl.glUniform2f(self.u_page_size, float(hw), float(border_t))
                            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                            # Bottom Edge
                            gl.glUniform2f(self.u_page_pos, float(hx), float(hy + hh - border_t))
                            gl.glUniform2f(self.u_page_size, float(hw), float(border_t))
                            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                            # Left Edge
                            gl.glUniform2f(self.u_page_pos, float(hx), float(hy))
                            gl.glUniform2f(self.u_page_size, float(border_t), float(hh))
                            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                            # Right Edge
                            gl.glUniform2f(self.u_page_pos, float(hx + hw - border_t), float(hy))
                            gl.glUniform2f(self.u_page_size, float(border_t), float(hh))
                            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
                else:
                    # Draw a nice loading placeholder (grey)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    gl.glUniform1i(self.u_is_placeholder, 2)
                    gl.glUniform4f(self.u_color, 0.95, 0.95, 0.95, 1.0)
                    gl.glUniform2f(self.u_page_pos, float(x_offset), float(page_y0))
                    gl.glUniform2f(self.u_page_size, float(dw), float(dh))
                    gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)

        # Housekeeping: delete textures for surfaces that have been evicted from RenderCache
        evicted = [s for s in self.textures if s not in active_surfaces]
        for s in evicted:
            tex_id = self.textures.pop(s)
            gl.glDeleteTextures([tex_id])

        gl.glBindVertexArray(0)
        gl.glUseProgram(0)
        gl.glDisable(gl.GL_BLEND)
        
        return True
