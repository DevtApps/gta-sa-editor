"""Static textured DFF preview using an OpenGL 2.1 compatibility context."""
import ctypes
import math

from OpenGL import GL
from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget


class Viewer3D(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setDepthBufferSize(24)
        self.setFormat(fmt)
        self.meshes = []
        self.images = {}
        self.textures = {}
        self.arrays = []
        self.dirty = False
        self.yaw, self.pitch, self.distance = 30, -65, 3
        self.center = [0, 0, 0]
        self.radius = 1
        self.last = None
        self.wireframe = False

    def set_scene(self, meshes, images):
        self.meshes, self.images = meshes, images
        self.arrays = []
        for mesh in meshes:
            vertices = (ctypes.c_float * len(mesh.positions))(*mesh.positions)
            indices = (ctypes.c_uint * len(mesh.indices))(*mesh.indices)
            uv = (ctypes.c_float * len(mesh.texcoords[0]))(*mesh.texcoords[0]) if mesh.texcoords else None
            normals = [0.0] * len(mesh.positions)
            for start in range(0, len(mesh.indices), 3):
                a, b, c = [index * 3 for index in mesh.indices[start:start + 3]]
                u = [mesh.positions[b + i] - mesh.positions[a + i] for i in range(3)]
                v = [mesh.positions[c + i] - mesh.positions[a + i] for i in range(3)]
                normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                for vertex in (a, b, c):
                    for axis in range(3):
                        normals[vertex + axis] += normal[axis]
            for start in range(0, len(normals), 3):
                length = math.sqrt(sum(value * value for value in normals[start:start + 3])) or 1
                normals[start:start + 3] = [value / length for value in normals[start:start + 3]]
            normal_array = (ctypes.c_float * len(normals))(*normals)
            self.arrays.append((vertices, indices, uv, normal_array))
        if meshes:
            low = [min(min(m.positions[i::3]) for m in meshes) for i in range(3)]
            high = [max(max(m.positions[i::3]) for m in meshes) for i in range(3)]
            self.center = [(a + b) / 2 for a, b in zip(low, high)]
            self.radius = max(math.dist(low, high) / 2, 0.01)
        self.reset_camera()
        self.dirty = True
        self.update()

    def reset_camera(self):
        self.yaw, self.pitch, self.distance = 30, -65, 3
        self.update()

    def initializeGL(self):
        GL.glClearColor(0.10, 0.12, 0.15, 1)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glEnable(GL.GL_ALPHA_TEST)
        GL.glAlphaFunc(GL.GL_GREATER, 0.05)
        GL.glEnable(GL.GL_LIGHT0)
        GL.glEnable(GL.GL_COLOR_MATERIAL)
        GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE)
        GL.glLightModelfv(GL.GL_LIGHT_MODEL_AMBIENT, [0.45, 0.45, 0.45, 1])
        GL.glLightModeli(GL.GL_LIGHT_MODEL_TWO_SIDE, GL.GL_TRUE)
        GL.glEnable(GL.GL_NORMALIZE)
        self.context().aboutToBeDestroyed.connect(self.cleanup)
        self.dirty = True

    def cleanup(self):
        self.makeCurrent()
        if self.textures:
            GL.glDeleteTextures(list(self.textures.values()))
        self.textures = {}
        self.doneCurrent()

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if not self.meshes:
            return
        if self.dirty:
            if self.textures:
                GL.glDeleteTextures(list(self.textures.values()))
            self.textures = {}
            for name, image in self.images.items():
                texture = GL.glGenTextures(1)
                self.textures[name] = texture
                GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
                GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, image.width, image.height, 0,
                                GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, image.tobytes())
            self.dirty = False
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        near = 0.01
        top = near * math.tan(math.radians(45) / 2)
        aspect = self.width() / max(1, self.height())
        GL.glFrustum(-top * aspect, top * aspect, -top, top, near, 100)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, [3, 4, 5, 0])
        if self.wireframe:
            GL.glDisable(GL.GL_LIGHTING)
        else:
            GL.glEnable(GL.GL_LIGHTING)
        GL.glTranslatef(0, 0, -self.distance)
        GL.glRotatef(self.pitch, 1, 0, 0)
        GL.glRotatef(self.yaw, 0, 0, 1)
        GL.glScalef(1 / self.radius, 1 / self.radius, 1 / self.radius)
        GL.glTranslatef(*[-value for value in self.center])
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE if self.wireframe else GL.GL_FILL)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        for mesh, (vertices, indices, uv, normals) in zip(self.meshes, self.arrays):
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, vertices)
            GL.glNormalPointer(GL.GL_FLOAT, 0, normals)
            texture = self.textures.get(mesh.texture_name.casefold())
            if texture and uv is not None and not self.wireframe:
                GL.glEnable(GL.GL_TEXTURE_2D)
                GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)
                GL.glTexCoordPointer(2, GL.GL_FLOAT, 0, uv)
                GL.glColor4f(*[v / 255 for v in mesh.diffuse_color])
            else:
                GL.glDisable(GL.GL_TEXTURE_2D)
                GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)
                GL.glColor4f(*[v / 255 for v in mesh.diffuse_color])
            GL.glDrawElements(GL.GL_TRIANGLES, len(mesh.indices), GL.GL_UNSIGNED_INT, indices)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDisableClientState(GL.GL_TEXTURE_COORD_ARRAY)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)

    def mousePressEvent(self, event):
        self.last = event.position()

    def mouseMoveEvent(self, event):
        if self.last is not None and event.buttons() & Qt.LeftButton:
            delta = event.position() - self.last
            self.yaw += delta.x() * 0.5
            self.pitch = max(-180, min(180, self.pitch + delta.y() * 0.5))
            self.last = event.position()
            self.update()

    def wheelEvent(self, event):
        self.distance = max(0.3, min(30, self.distance * math.exp(-event.angleDelta().y() / 1200)))
        self.update()
