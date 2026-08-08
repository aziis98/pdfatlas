class PageTexture:
    """Raw page pixels ready for direct GPU upload, bypassing cairo entirely.

    Holds tightly-packed ``samples`` bytes (stride == width * channels, as
    produced by PyMuPDF ``pix.samples``) so the object itself owns its memory.
    The GL canvas uploads these straight as ``GL_RGB`` with no channel swap.
    """

    __slots__ = ("width", "height", "channels", "samples")

    def __init__(self, width: int, height: int, channels: int, samples: bytes):
        self.width = width
        self.height = height
        self.channels = channels
        self.samples = samples

    @property
    def byte_size(self) -> int:
        return self.width * self.height * self.channels
