#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D u_texture;
uniform int u_is_placeholder;
uniform vec4 u_color;

void main() {
    if (u_is_placeholder == 1) {
        FragColor = vec4(1.0, 1.0, 1.0, 1.0);
    } else if (u_is_placeholder == 2) {
        FragColor = u_color;
    } else {
        vec4 tex = texture(u_texture, TexCoord);
        FragColor = vec4(tex.b, tex.g, tex.r, tex.a);
    }
}
