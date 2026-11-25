# -*- coding: utf-8 -*-
"""
DataPlot_fvm3d_post.py
3D FVM Postprocessing & Embedded Qt/Matplotlib CAE-Style Visualization Module.
Pure NumPy trilinear interpolation (zero SciPy dependency); all rendering functions
operate directly on caller-provided Matplotlib Figure/Axes without pyplot global state.
"""

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from matplotlib import cm, gridspec
from matplotlib.colors import Normalize
import matplotlib.pyplot as plt

from DataPlot_fvm3d_engine import reconstruct_boundary_wall_temperature


class TrilinearGridInterpolator:
    """Pure NumPy vectorized trilinear interpolator on a rectilinear 3D grid (x, y, z)."""

    def __init__(self, x: np.ndarray, y: np.ndarray, z: np.ndarray, values: np.ndarray):
        self.x = np.asarray(x, dtype=np.float64)
        self.y = np.asarray(y, dtype=np.float64)
        self.z = np.asarray(z, dtype=np.float64)
        self.v = np.asarray(values, dtype=np.float64)

    def __call__(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        px = np.clip(pts[:, 0], self.x[0], self.x[-1])
        py = np.clip(pts[:, 1], self.y[0], self.y[-1])
        pz = np.clip(pts[:, 2], self.z[0], self.z[-1])

        ix1 = np.clip(np.searchsorted(self.x, px, side='right'), 1, len(self.x) - 1)
        iy1 = np.clip(np.searchsorted(self.y, py, side='right'), 1, len(self.y) - 1)
        iz1 = np.clip(np.searchsorted(self.z, pz, side='right'), 1, len(self.z) - 1)
        ix0, iy0, iz0 = ix1 - 1, iy1 - 1, iz1 - 1

        dx = self.x[ix1] - self.x[ix0]
        dy = self.y[iy1] - self.y[iy0]
        dz = self.z[iz1] - self.z[iz0]
        wx = np.where(dx > 0.0, (px - self.x[ix0]) / np.where(dx > 0.0, dx, 1.0), 0.0)
        wy = np.where(dy > 0.0, (py - self.y[iy0]) / np.where(dy > 0.0, dy, 1.0), 0.0)
        wz = np.where(dz > 0.0, (pz - self.z[iz0]) / np.where(dz > 0.0, dz, 1.0), 0.0)

        c000 = self.v[ix0, iy0, iz0]
        c100 = self.v[ix1, iy0, iz0]
        c010 = self.v[ix0, iy1, iz0]
        c110 = self.v[ix1, iy1, iz0]
        c001 = self.v[ix0, iy0, iz1]
        c101 = self.v[ix1, iy0, iz1]
        c011 = self.v[ix0, iy1, iz1]
        c111 = self.v[ix1, iy1, iz1]

        c00 = c000 * (1.0 - wx) + c100 * wx
        c10 = c010 * (1.0 - wx) + c110 * wx
        c01 = c001 * (1.0 - wx) + c101 * wx
        c11 = c011 * (1.0 - wx) + c111 * wx
        c0 = c00 * (1.0 - wy) + c10 * wy
        c1 = c01 * (1.0 - wy) + c11 * wy
        return c0 * (1.0 - wz) + c1 * wz


def reconstruct_3d_field_with_walls(
    mesh: Dict[str, Any],
    T_cell: np.ndarray,
    material_props: Optional[Dict[str, float]] = None,
    boundary_conditions: Optional[Dict[str, Dict[str, Any]]] = None,
):
    """Build augmented (nx+2, ny+2, nz+2) field including exact physical wall temperatures."""
    nx, ny, nz = mesh['nx'], mesh['ny'], mesh['nz']
    x_min, x_max = mesh['x_range']
    y_min, y_max = mesh['y_range']
    z_min, z_max = mesh['z_range']

    x_ext = np.concatenate([[x_min], mesh['xc'], [x_max]])
    y_ext = np.concatenate([[y_min], mesh['yc'], [y_max]])
    z_ext = np.concatenate([[z_min], mesh['zc'], [z_max]])

    T_3d = T_cell.reshape((nx, ny, nz))
    T_ext = np.empty((nx + 2, ny + 2, nz + 2), dtype=np.float64)
    T_ext[1:-1, 1:-1, 1:-1] = T_3d

    if material_props is not None and boundary_conditions is not None:
        wall_T = reconstruct_boundary_wall_temperature(T_cell, mesh, material_props, boundary_conditions)
        T_ext[1:-1, 1:-1, 0]  = wall_T['bottom'].reshape((nx, ny))
        T_ext[1:-1, 1:-1, -1] = wall_T['top'].reshape((nx, ny))
        T_ext[0, 1:-1, 1:-1]  = wall_T['left'].reshape((ny, nz))
        T_ext[-1, 1:-1, 1:-1] = wall_T['right'].reshape((ny, nz))
        T_ext[1:-1, 0, 1:-1]  = wall_T['front'].reshape((nx, nz))
        T_ext[1:-1, -1, 1:-1] = wall_T['back'].reshape((nx, nz))
    else:
        T_ext[1:-1, 1:-1, 0]  = T_3d[:, :, 0]
        T_ext[1:-1, 1:-1, -1] = T_3d[:, :, -1]
        T_ext[0, 1:-1, 1:-1]  = T_3d[0, :, :]
        T_ext[-1, 1:-1, 1:-1] = T_3d[-1, :, :]
        T_ext[1:-1, 0, 1:-1]  = T_3d[:, 0, :]
        T_ext[1:-1, -1, 1:-1] = T_3d[:, -1, :]

    # 12 edges
    T_ext[0, 1:-1, 0]   = 0.5 * (T_ext[1, 1:-1, 0]   + T_ext[0, 1:-1, 1])
    T_ext[-1, 1:-1, 0]  = 0.5 * (T_ext[-2, 1:-1, 0]  + T_ext[-1, 1:-1, 1])
    T_ext[0, 1:-1, -1]  = 0.5 * (T_ext[1, 1:-1, -1]  + T_ext[0, 1:-1, -2])
    T_ext[-1, 1:-1, -1] = 0.5 * (T_ext[-2, 1:-1, -1] + T_ext[-1, 1:-1, -2])

    T_ext[1:-1, 0, 0]   = 0.5 * (T_ext[1:-1, 1, 0]   + T_ext[1:-1, 0, 1])
    T_ext[1:-1, -1, 0]  = 0.5 * (T_ext[1:-1, -2, 0]  + T_ext[1:-1, -1, 1])
    T_ext[1:-1, 0, -1]  = 0.5 * (T_ext[1:-1, 1, -1]  + T_ext[1:-1, 0, -2])
    T_ext[1:-1, -1, -1] = 0.5 * (T_ext[1:-1, -2, -1] + T_ext[1:-1, -1, -2])

    T_ext[0, 0, 1:-1]   = 0.5 * (T_ext[1, 0, 1:-1]   + T_ext[0, 1, 1:-1])
    T_ext[-1, 0, 1:-1]  = 0.5 * (T_ext[-2, 0, 1:-1]  + T_ext[-1, 1, 1:-1])
    T_ext[0, -1, 1:-1]  = 0.5 * (T_ext[1, -1, 1:-1]  + T_ext[0, -2, 1:-1])
    T_ext[-1, -1, 1:-1] = 0.5 * (T_ext[-2, -1, 1:-1] + T_ext[-1, -2, 1:-1])

    # 8 corners
    for ix, sx in ((0, 1), (-1, -2)):
        for iy, sy in ((0, 1), (-1, -2)):
            for iz, sz in ((0, 1), (-1, -2)):
                T_ext[ix, iy, iz] = (T_ext[sx, iy, iz] + T_ext[ix, sy, iz] + T_ext[ix, iy, sz]) / 3.0

    interp_3d = TrilinearGridInterpolator(x_ext, y_ext, z_ext, T_ext)
    return x_ext, y_ext, z_ext, T_ext, interp_3d


def _draw_wireframe_box_edges(ax, x_range, y_range, z_range, color='#1a1a1a', lw=1.2, alpha=0.85, visible_only=False):
    x0, x1 = x_range
    y0, y1 = y_range
    z0, z1 = z_range
    if visible_only:
        edges = [
            ([x0, x1], [y0, y0], [z1, z1]), ([x1, x1], [y0, y1], [z1, z1]),
            ([x1, x0], [y1, y1], [z1, z1]), ([x0, x0], [y1, y0], [z1, z1]),
            ([x0, x1], [y0, y0], [z0, z0]), ([x1, x1], [y0, y1], [z0, z0]),
            ([x0, x0], [y0, y0], [z0, z1]), ([x1, x1], [y0, y0], [z0, z1]),
            ([x1, x1], [y1, y1], [z0, z1]),
        ]
    else:
        edges = [
            ([x0, x1], [y0, y0], [z0, z0]), ([x1, x1], [y0, y1], [z0, z0]),
            ([x1, x0], [y1, y1], [z0, z0]), ([x0, x0], [y1, y0], [z0, z0]),
            ([x0, x1], [y0, y0], [z1, z1]), ([x1, x1], [y0, y1], [z1, z1]),
            ([x1, x0], [y1, y1], [z1, z1]), ([x0, x0], [y1, y0], [z1, z1]),
            ([x0, x0], [y0, y0], [z0, z1]), ([x1, x1], [y0, y0], [z0, z1]),
            ([x1, x1], [y1, y1], [z0, z1]), ([x0, x0], [y1, y1], [z0, z1]),
        ]
    for ex, ey, ez in edges:
        ax.plot(ex, ey, ez, color=color, linewidth=lw, alpha=alpha, zorder=20)


def render_cae_3d_solid(
    ax, mesh: Dict[str, Any], T_cell: np.ndarray,
    material_props: Optional[Dict[str, float]] = None,
    boundary_conditions: Optional[Dict[str, Dict[str, Any]]] = None,
    cmap_name: str = 'jet', elev: float = 24, azim: float = -52,
    n_fine: int = 48, true_aspect: bool = True,
    show_mesh_edges: bool = True, annotate_extrema: bool = True,
):
    """Render CAE-style 3D solid temperature contour onto an existing 3D Axes."""
    x_min, x_max = mesh['x_range']
    y_min, y_max = mesh['y_range']
    z_min, z_max = mesh['z_range']
    Lx, Ly, Lz = x_max - x_min, y_max - y_min, z_max - z_min

    _, _, _, T_ext, interp_3d = reconstruct_3d_field_with_walls(mesh, T_cell, material_props, boundary_conditions)
    T_min, T_max = float(np.min(T_ext)), float(np.max(T_ext))
    norm = Normalize(vmin=T_min, vmax=T_max if T_max > T_min + 1e-12 else T_min + 1.0)
    cmap = plt.get_cmap(cmap_name)

    xf = np.linspace(x_min, x_max, n_fine)
    yf = np.linspace(y_min, y_max, max(16, n_fine // 2))
    zf = np.linspace(z_min, z_max, n_fine)

    def plot_face(X, Y, Z, z_order=5):
        Xc = 0.25 * (X[:-1, :-1] + X[1:, :-1] + X[:-1, 1:] + X[1:, 1:])
        Yc = 0.25 * (Y[:-1, :-1] + Y[1:, :-1] + Y[:-1, 1:] + Y[1:, 1:])
        Zc = 0.25 * (Z[:-1, :-1] + Z[1:, :-1] + Z[:-1, 1:] + Z[1:, 1:])
        Tc = interp_3d(np.column_stack([Xc.ravel(), Yc.ravel(), Zc.ravel()])).reshape(Xc.shape)
        ax.plot_surface(
            X, Y, Z, facecolors=cmap(norm(Tc)),
            rstride=1, cstride=1, shade=False, antialiased=True,
            linewidth=0.12 if show_mesh_edges else 0.0,
            edgecolor=(0, 0, 0, 0.06) if show_mesh_edges else 'none',
            zorder=z_order,
        )

    X_xz, Z_xz = np.meshgrid(xf, zf, indexing='ij')
    plot_face(X_xz, np.full_like(X_xz, y_min), Z_xz, z_order=6)
    Y_yz, Z_yz = np.meshgrid(yf, zf, indexing='ij')
    plot_face(np.full_like(Y_yz, x_max), Y_yz, Z_yz, z_order=7)
    X_xy, Y_xy = np.meshgrid(xf, yf, indexing='ij')
    plot_face(X_xy, Y_xy, np.full_like(X_xy, z_max), z_order=8)

    _draw_wireframe_box_edges(ax, (x_min, x_max), (y_min, y_max), (z_min, z_max), visible_only=True)

    if annotate_extrema:
        ax.text2D(
            0.50, 1.01,
            f"Max: {np.max(T_cell):.2f} °C  |  Min: {np.min(T_cell):.2f} °C  |  dT: {np.max(T_cell)-np.min(T_cell):.2f} °C",
            transform=ax.transAxes, ha='center', va='bottom',
            fontsize=9.5, fontweight='bold', color='#1e293b',
            bbox=dict(boxstyle='round,pad=0.3', facecolor='#f8fafc', edgecolor='#cbd5e1', alpha=0.95),
        )

    ax.set_xlabel('x (m)', labelpad=6)
    ax.set_ylabel('y (m)', labelpad=6)
    ax.set_zlabel('z (m)', labelpad=6)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_zlim(z_min, z_max)
    if true_aspect:
        ax.set_box_aspect((Lx, max(Ly * 2.0, 0.035), Lz))
    else:
        ax.set_box_aspect((1.0, 0.5, 1.1))
    ax.view_init(elev=elev, azim=azim)
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False

    mappable = cm.ScalarMappable(norm=norm, cmap=cmap)
    mappable.set_array([])
    return mappable


def render_3d_overview_on_fig(fig, thermal: Dict[str, Any], snap_idx: int = -1, cmap_name: str = 'jet'):
    """Render 1x2 overview: Left = Tmin/Tavg/Tmax + Qh, Right = 3D CAE solid."""
    fig.clf()
    gs = gridspec.GridSpec(1, 2, figure=fig, width_ratios=[1.05, 1.0], wspace=0.18)

    t_hist = thermal['time_history']
    stats = thermal['stats']
    q_hist = thermal.get('q_history', None)
    T_snaps = thermal['T_snapshots']
    t_snaps = thermal['t_snapshots']
    idx = max(0, min(len(T_snaps) - 1, snap_idx if snap_idx >= 0 else len(T_snaps) - 1))
    T_sel = T_snaps[idx]
    t_sel = t_snaps[idx]

    ax1 = fig.add_subplot(gs[0, 0])
    l1 = ax1.plot(t_hist, stats['max'], color='#d62728', lw=2.0, label='最高温 T_max')[0]
    l2 = ax1.plot(t_hist, stats['avg'], color='#2ca02c', lw=2.0, label='体积均温 T_avg')[0]
    l3 = ax1.plot(t_hist, stats['min'], color='#1f77b4', lw=2.0, label='最低温 T_min')[0]
    ax1.axvline(t_sel, color='#64748b', linestyle=':', lw=1.2)
    ax1.set_xlabel('时间 Time (s)', fontsize=10.5)
    ax1.set_ylabel('温度 Temperature (°C)', fontsize=10.5)
    ax1.set_title('3D 温度统计演化与产热功率', fontsize=12, fontweight='bold')
    ax1.grid(True, linestyle='--', alpha=0.5)
    lines = [l1, l2, l3]
    if q_hist is not None and len(q_hist) == len(t_hist):
        ax1r = ax1.twinx()
        l4 = ax1r.plot(t_hist, q_hist, color='#334155', linestyle='--', lw=1.4, alpha=0.8, label='总产热功率 Q (W)')[0]
        ax1r.set_ylabel('产热功率 Q (W)', fontsize=10.5, color='#334155')
        lines.append(l4)
    ax1.legend(lines, [l.get_label() for l in lines], loc='best', fontsize=9, frameon=True)

    ax2 = fig.add_subplot(gs[0, 1], projection='3d')
    mappable = render_cae_3d_solid(
        ax2, thermal['mesh'], T_sel,
        material_props=thermal['material_props'],
        boundary_conditions=thermal['boundary_conditions'],
        cmap_name=cmap_name, n_fine=44,
    )
    fig.colorbar(mappable, ax=ax2, label='Temperature (°C)', shrink=0.68, aspect=18, pad=0.06)
    ax2.set_title(f'3D 实体温度场 (t = {t_sel:.1f} s)', fontsize=12, fontweight='bold', pad=22)


def render_cae_3d_cutaway_on_fig(fig, thermal: Dict[str, Any], snap_idx: int = -1, cmap_name: str = 'jet'):
    """Render 1x3 CAE multi-view: Isometric Solid | 3D Orthogonal Cut-Planes | 2D Mid-Plane Contours."""
    fig.clf()
    mesh = thermal['mesh']
    T_snaps = thermal['T_snapshots']
    t_snaps = thermal['t_snapshots']
    idx = max(0, min(len(T_snaps) - 1, snap_idx if snap_idx >= 0 else len(T_snaps) - 1))
    T_sel = T_snaps[idx]
    t_sel = t_snaps[idx]

    x_min, x_max = mesh['x_range']
    y_min, y_max = mesh['y_range']
    z_min, z_max = mesh['z_range']
    Lx, Ly, Lz = x_max - x_min, y_max - y_min, z_max - z_min

    _, _, _, T_ext, interp_3d = reconstruct_3d_field_with_walls(
        mesh, T_sel, thermal['material_props'], thermal['boundary_conditions']
    )
    T_min, T_max = float(np.min(T_ext)), float(np.max(T_ext))
    norm = Normalize(vmin=T_min, vmax=T_max if T_max > T_min + 1e-12 else T_min + 1.0)
    cmap = plt.get_cmap(cmap_name)

    gs = gridspec.GridSpec(1, 3, figure=fig, width_ratios=[1.1, 1.1, 0.9], wspace=0.22)

    ax1 = fig.add_subplot(gs[0, 0], projection='3d')
    render_cae_3d_solid(
        ax1, mesh, T_sel, thermal['material_props'], thermal['boundary_conditions'],
        cmap_name=cmap_name, n_fine=40, annotate_extrema=True,
    )
    ax1.set_title(f'1. 3D 外表面云图 (t={t_sel:.1f}s)', fontsize=11, fontweight='bold', pad=22)

    ax2 = fig.add_subplot(gs[0, 1], projection='3d')
    xf = np.linspace(x_min, x_max, 42)
    yf = np.linspace(y_min, y_max, 20)
    zf = np.linspace(z_min, z_max, 42)

    def plot_slice(X, Y, Z):
        Xc = 0.25 * (X[:-1, :-1] + X[1:, :-1] + X[:-1, 1:] + X[1:, 1:])
        Yc = 0.25 * (Y[:-1, :-1] + Y[1:, :-1] + Y[:-1, 1:] + Y[1:, 1:])
        Zc = 0.25 * (Z[:-1, :-1] + Z[1:, :-1] + Z[:-1, 1:] + Z[1:, 1:])
        Tc = interp_3d(np.column_stack([Xc.ravel(), Yc.ravel(), Zc.ravel()])).reshape(Xc.shape)
        ax2.plot_surface(X, Y, Z, facecolors=cmap(norm(Tc)), rstride=1, cstride=1,
                         shade=False, antialiased=True, alpha=0.92, edgecolor='none')

    X_xz, Z_xz = np.meshgrid(xf, zf, indexing='ij')
    plot_slice(X_xz, np.full_like(X_xz, 0.5 * (y_min + y_max)), Z_xz)
    Y_yz, Z_yz = np.meshgrid(yf, zf, indexing='ij')
    plot_slice(np.full_like(Y_yz, 0.5 * (x_min + x_max)), Y_yz, Z_yz)
    X_xy, Y_xy = np.meshgrid(xf, yf, indexing='ij')
    plot_slice(X_xy, Y_xy, np.full_like(X_xy, z_min + 0.25 * Lz))
    _draw_wireframe_box_edges(ax2, (x_min, x_max), (y_min, y_max), (z_min, z_max), color='#333333', lw=1.0, alpha=0.6)
    ax2.set_xlabel('x (m)', labelpad=6)
    ax2.set_ylabel('y (m)', labelpad=6)
    ax2.set_zlabel('z (m)', labelpad=6)
    ax2.set_xlim(x_min, x_max)
    ax2.set_ylim(y_min, y_max)
    ax2.set_zlim(z_min, z_max)
    ax2.set_box_aspect((Lx, max(Ly * 2.0, 0.035), Lz))
    ax2.view_init(elev=24, azim=-52)
    ax2.xaxis.pane.fill = False
    ax2.yaxis.pane.fill = False
    ax2.zaxis.pane.fill = False
    ax2.set_title('2. 内部正交剖切面', fontsize=11, fontweight='bold', pad=22)

    ax3 = fig.add_subplot(gs[0, 2])
    pts_mid = np.column_stack([X_xz.ravel(), np.full(X_xz.size, 0.5 * (y_min + y_max)), Z_xz.ravel()])
    T_mid = interp_3d(pts_mid).reshape(X_xz.shape)
    cf = ax3.contourf(X_xz, Z_xz, T_mid, levels=32, cmap=cmap, norm=norm)
    if T_max > T_min + 0.05:
        cs = ax3.contour(X_xz, Z_xz, T_mid, levels=10, colors='k', linewidths=0.65, alpha=0.6)
        ax3.clabel(cs, inline=True, fontsize=8, fmt='%.1f')
    ax3.set_xlabel('x (m)', fontsize=10)
    ax3.set_ylabel('z (m)', fontsize=10)
    ax3.set_aspect('equal')
    ax3.set_title(f'3. 厚度中面等温线 (y={0.5*(y_min+y_max)*1000:.1f}mm)', fontsize=11, fontweight='bold')

    fig.colorbar(cf, ax=[ax1, ax2, ax3], label='Temperature (°C)', shrink=0.78, aspect=22, pad=0.03)


def render_6_surfaces_on_fig(fig, thermal: Dict[str, Any], snap_idx: int = -1, cmap_name: str = 'jet'):
    """Render 2x3 flat temperature contour maps of all 6 exterior surfaces at selected snapshot."""
    fig.clf()
    mesh = thermal['mesh']
    T_snaps = thermal['T_snapshots']
    t_snaps = thermal['t_snapshots']
    idx = max(0, min(len(T_snaps) - 1, snap_idx if snap_idx >= 0 else len(T_snaps) - 1))
    T_sel = T_snaps[idx]
    t_sel = t_snaps[idx]

    nx, ny, nz = mesh['nx'], mesh['ny'], mesh['nz']
    xc, yc, zc = mesh['xc'], mesh['yc'], mesh['zc']
    wall_T = reconstruct_boundary_wall_temperature(
        T_sel, mesh, thermal['material_props'], thermal['boundary_conditions']
    )
    surfs = {
        'bottom': (xc, yc, wall_T['bottom'].reshape((nx, ny)), '底面 Bottom (z=0)', 'x (m)', 'y (m)'),
        'top':    (xc, yc, wall_T['top'].reshape((nx, ny)),    '顶面 Top (z=Lz)',   'x (m)', 'y (m)'),
        'front':  (xc, zc, wall_T['front'].reshape((nx, nz)),  '正面 Front (y=0)',  'x (m)', 'z (m)'),
        'back':   (xc, zc, wall_T['back'].reshape((nx, nz)),   '背面 Back (y=Ly)',  'x (m)', 'z (m)'),
        'left':   (yc, zc, wall_T['left'].reshape((ny, nz)),   '左面 Left (x=0)',   'y (m)', 'z (m)'),
        'right':  (yc, zc, wall_T['right'].reshape((ny, nz)),  '右面 Right (x=Lx)', 'y (m)', 'z (m)'),
    }
    T_min = min(float(np.min(v[2])) for v in surfs.values())
    T_max = max(float(np.max(v[2])) for v in surfs.values())
    if np.isclose(T_min, T_max):
        T_min -= 0.5
        T_max += 0.5

    cmap = plt.get_cmap(cmap_name)
    fig.suptitle(f'电芯六表面温度展开云图 (t = {t_sel:.1f} s)', fontsize=13, fontweight='bold')
    for pos, key in enumerate(['bottom', 'top', 'front', 'back', 'left', 'right'], start=1):
        ax = fig.add_subplot(2, 3, pos)
        u, v, Tg, title, xl, yl = surfs[key]
        im = ax.pcolormesh(u, v, Tg.T, cmap=cmap, vmin=T_min, vmax=T_max, shading='auto')
        ax.set_title(f'{title} [{np.min(Tg):.1f}~{np.max(Tg):.1f}°C]', fontsize=10)
        ax.set_xlabel(xl, fontsize=9)
        ax.set_ylabel(yl, fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.82, aspect=16)
    fig.tight_layout(rect=[0, 0, 1, 0.94])


def export_vtk_structured_grid(
    mesh: Dict[str, Any], T_cell: np.ndarray, save_path: str,
    material_props: Optional[Dict[str, float]] = None,
    boundary_conditions: Optional[Dict[str, Dict[str, Any]]] = None,
):
    """Export 3D FVM field to ParaView-compatible legacy VTK RECTILINEAR_GRID file."""
    nx, ny, nz = mesh['nx'], mesh['ny'], mesh['nz']
    x_faces, y_faces, z_faces = mesh['x_faces'], mesh['y_faces'], mesh['z_faces']
    _, _, _, _, interp_3d = reconstruct_3d_field_with_walls(mesh, T_cell, material_props, boundary_conditions)

    XV, YV, ZV = np.meshgrid(x_faces, y_faces, z_faces, indexing='ij')
    pts_vtk = np.column_stack([
        XV.transpose(2, 1, 0).ravel(),
        YV.transpose(2, 1, 0).ravel(),
        ZV.transpose(2, 1, 0).ravel(),
    ])
    T_points_vtk = interp_3d(pts_vtk)
    T_cells_vtk = T_cell.reshape((nx, ny, nz)).transpose(2, 1, 0).ravel()

    with open(save_path, 'w', encoding='utf-8') as f:
        f.write("# vtk DataFile Version 3.0\n")
        f.write("DataPlot 3D FVM Thermal Simulation Result\n")
        f.write("ASCII\n")
        f.write("DATASET RECTILINEAR_GRID\n")
        f.write(f"DIMENSIONS {nx + 1} {ny + 1} {nz + 1}\n")
        f.write(f"X_COORDINATES {nx + 1} double\n")
        f.write(" ".join(f"{v:.6e}" for v in x_faces) + "\n")
        f.write(f"Y_COORDINATES {ny + 1} double\n")
        f.write(" ".join(f"{v:.6e}" for v in y_faces) + "\n")
        f.write(f"Z_COORDINATES {nz + 1} double\n")
        f.write(" ".join(f"{v:.6e}" for v in z_faces) + "\n")
        f.write(f"\nCELL_DATA {nx * ny * nz}\n")
        f.write("SCALARS Cell_Temperature double 1\n")
        f.write("LOOKUP_TABLE default\n")
        np.savetxt(f, T_cells_vtk, fmt="%.6f")
        f.write(f"\nPOINT_DATA {(nx + 1) * (ny + 1) * (nz + 1)}\n")
        f.write("SCALARS Nodal_Temperature double 1\n")
        f.write("LOOKUP_TABLE default\n")
        np.savetxt(f, T_points_vtk, fmt="%.6f")
