# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native viewer observation contracts, independent of the Agent Bridge add-on."""

import os
import unittest
import uuid

import FreeCAD as App
import FreeCADGui as Gui
from pivy import coin
from TestViewerCoordinationBuild import EXPECT_CLIPPING_WRITE_REVISION
try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui as QtWidgets


class TestViewerCoordination(unittest.TestCase):
    def setUp(self):
        self.window = Gui.getMainWindow()
        self.window_was_visible = self.window.isVisible()
        self.window.show()
        self.previous = App.ActiveDocument
        self.doc = App.newDocument("ViewerCoordination_" + uuid.uuid4().hex[:8])
        self.view = Gui.getDocument(self.doc.Name).activeView()
        self.viewer = self.view.getViewer()
        self.view.setAnimationEnabled(False)

    def tearDown(self):
        if self.doc is not None:
            App.closeDocument(self.doc.Name)
        if self.previous is not None and self.previous.Name in App.listDocuments():
            App.setActiveDocument(self.previous.Name)
        if not self.window_was_visible:
            self.window.hide()

    def flush_gui(self):
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        QtWidgets.QApplication.processEvents()
        Gui.updateGui()

    def wait_for_paint(self):
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(80, loop.quit)
        loop.exec_()

    def clipping_query(self):
        query = getattr(self.viewer, "getClippingWriteRevision", None)
        if query is None:
            if EXPECT_CLIPPING_WRITE_REVISION or os.environ.get("FREECAD_EXPECT_CLIPPING_WRITE_REVISION") == "1":
                self.fail("Bundled Coin build lost getClippingWriteRevision")
            self.skipTest("External Coin without clipping write observation")
        return query

    def test_idle_query_is_read_only(self):
        camera = self.view.getCamera()
        revision = self.viewer.getAnimationSettingsRevision()
        for _ in range(20):
            self.assertIs(self.viewer.isAnimating(), False)
            self.assertEqual(self.viewer.getAnimationSettingsRevision(), revision)
        self.assertEqual(self.view.getCamera(), camera)

    def test_same_value_and_transient_setting_writes(self):
        query = self.viewer.getAnimationSettingsRevision
        before = query()
        self.assertIs(type(before), int)
        self.assertGreaterEqual(before, 0)
        self.view.setAnimationEnabled(False)
        self.assertEqual(query(), before + 1)
        self.view.setAnimationEnabled(True)
        self.view.setAnimationEnabled(False)
        self.assertEqual(query(), before + 3)

    def test_running_query_does_not_stop_spinning(self):
        self.view.setAnimationEnabled(True)
        self.view.startAnimating(0, 0, 1, 0.08)
        try:
            camera = self.view.getCamera()
            revision = self.viewer.getAnimationSettingsRevision()
            for _ in range(20):
                self.assertIs(self.viewer.isAnimating(), True)
            self.assertEqual(self.view.getCamera(), camera)
            self.assertEqual(self.viewer.getAnimationSettingsRevision(), revision)
        finally:
            self.view.stopAnimating()
        self.assertIs(self.viewer.isAnimating(), False)

    def test_running_query_detects_stationary_transition(self):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/View")
        original = preferences.GetInt("AnimationDuration", 250)
        existed = any(entry[1] == "AnimationDuration" for entry in preferences.GetContents())
        preferences.SetInt("AnimationDuration", 2000)
        try:
            self.view.setAnimationEnabled(True)
            self.view.setCameraOrientation(self.view.getCameraOrientation())
            camera = self.view.getCamera()
            self.assertIs(self.viewer.isAnimating(), True)
            self.assertFalse(self.viewer.isSpinning())
            self.assertEqual(self.view.getCamera(), camera)
        finally:
            self.view.stopAnimating()
            if existed:
                preferences.SetInt("AnimationDuration", original)
            else:
                preferences.RemInt("AnimationDuration")

    def test_navigation_replacement_preserves_monotonic_revision(self):
        original = self.view.getNavigationType()
        other = ("Gui::InventorNavigationStyle" if original != "Gui::InventorNavigationStyle"
                 else "Gui::CADNavigationStyle")
        before = self.viewer.getAnimationSettingsRevision()
        self.view.setNavigationType(other)
        changed = self.viewer.getAnimationSettingsRevision()
        self.assertGreater(changed, before)
        self.view.setNavigationType(original)
        self.assertGreater(self.viewer.getAnimationSettingsRevision(), changed)

    def test_noargs_then_varargs_dispatch(self):
        # These must be the first retrieved methods on this fresh wrapper.
        viewer = self.view.getViewer()
        self.assertIs(type(viewer.isAnimating()), bool)
        viewer.setEnabledNaviCube(viewer.isEnabledNaviCube())
        self.assertIs(type(viewer.getAnimationSettingsRevision()), int)

    def test_varargs_then_noargs_dispatch(self):
        viewer = self.view.getViewer()
        viewer.setEnabledNaviCube(False)
        self.assertIs(type(viewer.isAnimating()), bool)
        self.assertIs(type(viewer.getAnimationSettingsRevision()), int)

    def test_retained_queries_refuse_deleted_viewer(self):
        queries = [self.viewer.isAnimating, self.viewer.getAnimationSettingsRevision]
        if hasattr(self.viewer, "getClippingWriteRevision"):
            queries.append(self.viewer.getClippingWriteRevision)
        App.closeDocument(self.doc.Name)
        self.doc = None
        self.flush_gui()
        for query in queries:
            with self.subTest(query=query):
                with self.assertRaises(RuntimeError):
                    query()

    def test_clipping_query_and_direct_writes(self):
        query = self.clipping_query()
        camera = self.view.getCameraNode()
        before_camera = self.view.getCamera()
        revision = query()
        self.assertEqual(len(revision), 2)
        for value in revision:
            self.assertIs(type(value), int)
            self.assertGreaterEqual(value, 0)
        for _ in range(20):
            self.assertEqual(query(), revision)
        self.assertEqual(self.view.getCamera(), before_camera)
        for field, index in ((camera.nearDistance, 0), (camera.farDistance, 1)):
            before = query()
            old = field.getValue()
            field.setValue(old)  # Same-value writes carry user intent too.
            field.setValue(old + 1)
            field.setValue(old)
            expected = list(before)
            expected[index] += 3
            self.assertEqual(query(), tuple(expected))

    def test_clipping_suppressed_notifications_count(self):
        query = self.clipping_query()
        camera = self.view.getCameraNode()
        before = query()
        old = camera.nearDistance.enableNotify(False)
        try:
            camera.nearDistance.setValue(camera.nearDistance.getValue())
        finally:
            camera.nearDistance.enableNotify(old)
        self.assertEqual(query(), (before[0] + 1, before[1]))

    def test_automatic_clipping_and_reentrant_writes(self):
        try:
            import PartGui  # Register the box's native view provider in a clean session.
        except ImportError:
            self.skipTest("Automatic clipping scene requires the optional PartGui module")

        query = self.clipping_query()
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        self.assertGreater(box.Shape.Volume, 0)
        self.view.viewFront()
        self.view.fitAll()
        for _ in range(4):
            self.flush_gui()
            self.view.redraw()
            self.wait_for_paint()
        camera = self.view.getCameraNode()
        manager = self.viewer.getSoRenderManager()
        manager.setAutoClipping(coin.SoRenderManager.VARIABLE_NEAR_PLANE)
        camera.nearDistance.setValue(camera.nearDistance.getValue() + 50)
        baseline = query()
        notifications = []

        def on_clip(*args):
            if not notifications:
                # The renderer's exact assignment must not count, even before
                # its immediate callback; callback writes must count normally.
                notifications.append(query())
                camera.nearDistance.setValue(camera.nearDistance.getValue())
                camera.farDistance.setValue(camera.farDistance.getValue())

        sensor = coin.SoFieldSensor(on_clip, None)
        sensor.setPriority(0)
        sensor.attach(camera.nearDistance)
        try:
            # Synchronous native rendering avoids a queued startup repaint.
            image = self.viewer.renderToImage(width=240, height=180, samples=0)
            self.assertFalse(image.isNull(), "Expected a native OpenGL render")
            # renderToImage leaves the viewer's OpenGL context current. Its
            # framebuffer path applies an action directly; call the manager to
            # exercise the automatic clipping assignments themselves.
            manager.render(False, False)
            self.assertEqual(notifications, [baseline])
            self.assertEqual(query(), (baseline[0] + 1, baseline[1] + 1))
        finally:
            sensor.detach()
