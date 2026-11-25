# -*- coding: utf-8 -*-
"""
DataPlot_fvm3d_engine.py
3D Cell-Centered Finite Volume Method (FVM) Transient Thermal Solver.
Refactored from FVM_Thermal_OP_TPower (fvm_mesh_volume.py + fvm_solver.py):
  - Orthogonal anisotropic thermal conductivity (kx, ky, kz)
  - 6 independent surface boundary conditions (convection/robin, temperature/dirichlet,
    heat_flux/neumann, adiabatic/insulated)
  - Default zero-dependency Pure NumPy matrix-free 7-point Jacobi-PCG solver
  - Optional SciPy sparse direct solver (cached LU when dt & BCs are fixed)
  - Standalone runner (table / constant / time_func / temp_func) and
    FVM3DThermalStepper for bidirectional coupling with DataPlot_hgp_engine
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from scipy.sparse import csr_matrix, diags as sp_diags
    from scipy.sparse.linalg import factorized as sp_factorized, spsolve as sp_spsolve
    HAS_SCIPY_SPARSE = True
except Exception:
    HAS_SCIPY_SPARSE = False


# ==============================================================================
# 1. Mesh Generation & Boundary Conductance / Wall Temperature Reconstruction
# ==============================================================================

def generate_fvm_mesh(x_range, y_range, z_range, nx: int, ny: int, nz: int) -> Dict[str, Any]:
    """Generate structured 3D cell-centered Finite Volume mesh."""
    nx, ny, nz = int(nx), int(ny), int(nz)
    x_faces = np.linspace(float(x_range[0]), float(x_range[1]), nx + 1)
    y_faces = np.linspace(float(y_range[0]), float(y_range[1]), ny + 1)
    z_faces = np.linspace(float(z_range[0]), float(z_range[1]), nz + 1)

    dx = np.diff(x_faces)
    dy = np.diff(y_faces)
    dz = np.diff(z_faces)

    xc = 0.5 * (x_faces[:-1] + x_faces[1:])
    yc = 0.5 * (y_faces[:-1] + y_faces[1:])
    zc = 0.5 * (z_faces[:-1] + z_faces[1:])

    XC, YC, ZC = np.meshgrid(xc, yc, zc, indexing='ij')
    cell_centers = np.column_stack((XC.ravel(), YC.ravel(), ZC.ravel()))

    DX, DY, DZ = np.meshgrid(dx, dy, dz, indexing='ij')
    cell_volumes = (DX * DY * DZ).ravel()
    n_cells = int(nx * ny * nz)

    cell_grid = np.arange(n_cells, dtype=int).reshape((nx, ny, nz))
    area_xy = (dx[:, None] * dy[None, :]).ravel()
    area_yz = (dy[:, None] * dz[None, :]).ravel()
    area_xz = (dx[:, None] * dz[None, :]).ravel()

    boundary_cells = {
        'bottom': cell_grid[:, :, 0].ravel(),
        'top':    cell_grid[:, :, -1].ravel(),
        'left':   cell_grid[0, :, :].ravel(),
        'right':  cell_grid[-1, :, :].ravel(),
        'front':  cell_grid[:, 0, :].ravel(),
        'back':   cell_grid[:, -1, :].ravel(),
    }
    boundary_areas = {
        'bottom': area_xy, 'top': area_xy,
        'left':   area_yz, 'right': area_yz,
        'front':  area_xz, 'back': area_xz,
    }
    boundary_half_dist = {
        'bottom': np.full(nx * ny, 0.5 * dz[0]),
        'top':    np.full(nx * ny, 0.5 * dz[-1]),
        'left':   np.full(ny * nz, 0.5 * dx[0]),
        'right':  np.full(ny * nz, 0.5 * dx[-1]),
        'front':  np.full(nx * nz, 0.5 * dy[0]),
        'back':   np.full(nx * nz, 0.5 * dy[-1]),
    }

    return {
        'nx': nx, 'ny': ny, 'nz': nz,
        'n_cells': n_cells,
        'x_range': (float(x_range[0]), float(x_range[1])),
        'y_range': (float(y_range[0]), float(y_range[1])),
        'z_range': (float(z_range[0]), float(z_range[1])),
        'x_faces': x_faces, 'y_faces': y_faces, 'z_faces': z_faces,
        'xc': xc, 'yc': yc, 'zc': zc,
        'dx': dx, 'dy': dy, 'dz': dz,
        'cell_centers': cell_centers,
        'cell_volumes': cell_volumes,
        'cell_grid': cell_grid,
        'boundary_cells': boundary_cells,
        'boundary_areas': boundary_areas,
        'boundary_half_dist': boundary_half_dist,
    }


def compute_harmonic_conductance(k_lam: float, d_p, d_n, area):
    return k_lam * area / (d_p + d_n)


def compute_boundary_conductance(k_normal: float, h_conv: float, half_dist, area, exact_series: bool = True):
    if h_conv <= 0.0:
        return np.zeros_like(area, dtype=np.float64)
    if exact_series:
        return area / (1.0 / h_conv + half_dist / k_normal)
    return h_conv * area


def compute_dirichlet_conductance(k_normal: float, half_dist, area):
    return k_normal * area / half_dist


def reconstruct_boundary_wall_temperature(
    T_cell: np.ndarray,
    mesh: Dict[str, Any],
    material_props: Dict[str, float],
    boundary_conditions: Dict[str, Dict[str, Any]],
) -> Dict[str, np.ndarray]:
    """Reconstruct physical wall temperature T_wall on all 6 boundary faces."""
    wall_T = {}
    normal_k = {
        'bottom': float(material_props['kz']),
        'top':    float(material_props['kz']),
        'left':   float(material_props['kx']),
        'right':  float(material_props['kx']),
        'front':  float(material_props['ky']),
        'back':   float(material_props['ky']),
    }
    for bname, bc in boundary_conditions.items():
        cells = mesh['boundary_cells'][bname]
        half_d = mesh['boundary_half_dist'][bname]
        k_n = normal_k[bname]
        btype = str(bc.get('type', 'convection')).lower().strip()

        if btype in ('convection', 'robin'):
            h = float(bc['h'])
            T_inf = float(bc['T_inf'])
            g_cond = k_n / half_d
            wall_T[bname] = (g_cond * T_cell[cells] + h * T_inf) / (g_cond + h)
        elif btype in ('temperature', 'dirichlet', 'constant_temperature'):
            T_wall_val = float(bc['T_wall'] if 'T_wall' in bc else bc['T'])
            wall_T[bname] = np.full(len(cells), T_wall_val, dtype=np.float64)
        elif btype in ('heat_flux', 'neumann'):
            q_flux = float(bc.get('q_flux', bc.get('q', 0.0)))
            wall_T[bname] = T_cell[cells] + q_flux * half_d / k_n
        elif btype in ('adiabatic', 'insulated'):
            wall_T[bname] = T_cell[cells].copy()
        else:
            raise ValueError(f"Unsupported boundary condition type '{btype}' for '{bname}'.")
    return wall_T


def compute_temperature_statistics(T_history: List[np.ndarray], cell_volumes: Optional[np.ndarray] = None) -> Dict[str, List[float]]:
    stats = {'min': [], 'max': [], 'avg': [], 'std': []}
    weights = (cell_volumes / np.sum(cell_volumes)) if cell_volumes is not None else None
    for T in T_history:
        stats['min'].append(float(np.min(T)))
        stats['max'].append(float(np.max(T)))
        if weights is not None:
            m = float(np.sum(weights * T))
            s = float(np.sqrt(np.sum(weights * (T - m) ** 2)))
        else:
            m = float(np.mean(T))
            s = float(np.std(T))
        stats['avg'].append(m)
        stats['std'].append(s)
    return stats


# ==============================================================================
# 2. Matrix-Free 3D FVM Operator & Jacobi-PCG / Optional SciPy Direct Solver
# ==============================================================================

class FVM3DOperator:
    """
    Encapsulates the 3D FVM system M * dT/dt + K * T = qh * F_unit + F_b.
    Supports both zero-dependency Pure NumPy matrix-free Jacobi-PCG and
    optional SciPy sparse LU direct solver.
    """

    def __init__(
        self,
        mesh: Dict[str, Any],
        kx: float, ky: float, kz: float,
        rho: float, cp: float,
        boundary_conditions: Dict[str, Dict[str, Any]],
        exact_boundary_series: bool = True,
        solver_mode: str = 'numpy_pcg',
        pcg_tol: float = 1e-8,
        pcg_max_iter: int = 800,
    ):
        self.mesh = mesh
        self.nx, self.ny, self.nz = mesh['nx'], mesh['ny'], mesh['nz']
        self.n_cells = mesh['n_cells']
        self.kx, self.ky, self.kz = float(kx), float(ky), float(kz)
        self.rho, self.cp = float(rho), float(cp)
        self.exact_boundary_series = bool(exact_boundary_series)
        self.solver_mode = solver_mode if (solver_mode == 'scipy_direct' and HAS_SCIPY_SPARSE) else 'numpy_pcg'
        self.pcg_tol = float(pcg_tol)
        self.pcg_max_iter = int(pcg_max_iter)

        self.F_unit = mesh['cell_volumes'].copy()
        self.M_diag_1d = self.rho * self.cp * self.F_unit
        self.M_diag_3d = self.M_diag_1d.reshape((self.nx, self.ny, self.nz))

        dx, dy, dz = mesh['dx'], mesh['dy'], mesh['dz']
        # Precompute internal face conductances once
        if self.nx > 1:
            area_x = np.broadcast_to(dy[None, :, None] * dz[None, None, :], (self.nx - 1, self.ny, self.nz))
            self.Gx = compute_harmonic_conductance(self.kx, 0.5 * dx[:-1, None, None], 0.5 * dx[1:, None, None], area_x)
        else:
            self.Gx = None

        if self.ny > 1:
            area_y = np.broadcast_to(dx[:, None, None] * dz[None, None, :], (self.nx, self.ny - 1, self.nz))
            self.Gy = compute_harmonic_conductance(self.ky, 0.5 * dy[None, :-1, None], 0.5 * dy[None, 1:, None], area_y)
        else:
            self.Gy = None

        if self.nz > 1:
            area_z = np.broadcast_to(dx[:, None, None] * dy[None, :, None], (self.nx, self.ny, self.nz - 1))
            self.Gz = compute_harmonic_conductance(self.kz, 0.5 * dz[None, None, :-1], 0.5 * dz[None, None, 1:], area_z)
        else:
            self.Gz = None

        # Internal diagonal of K (independent of BCs)
        diag_int = np.zeros((self.nx, self.ny, self.nz), dtype=np.float64)
        if self.Gx is not None:
            diag_int[:-1, :, :] += self.Gx
            diag_int[1:, :, :] += self.Gx
        if self.Gy is not None:
            diag_int[:, :-1, :] += self.Gy
            diag_int[:, 1:, :] += self.Gy
        if self.Gz is not None:
            diag_int[:, :, :-1] += self.Gz
            diag_int[:, :, 1:] += self.Gz
        self._diag_K_internal_1d = diag_int.ravel()

        # Boundary-dependent state
        self.boundary_conditions: Dict[str, Dict[str, Any]] = {}
        self.diag_K_3d = np.zeros((self.nx, self.ny, self.nz), dtype=np.float64)
        self.F_b = np.zeros(self.n_cells, dtype=np.float64)

        # Optional SciPy sparse caches
        self._K_sparse = None
        self._M_sparse = None
        self._cached_lu = None
        self._cached_lu_key = None

        self.update_boundaries(boundary_conditions)

    def update_boundaries(self, boundary_conditions: Dict[str, Dict[str, Any]]):
        """Update boundary conditions without recomputing internal conductances."""
        self.boundary_conditions = {k: dict(v) for k, v in boundary_conditions.items()}
        diag_K_1d = self._diag_K_internal_1d.copy()
        F_b = np.zeros(self.n_cells, dtype=np.float64)

        normal_k = {
            'bottom': self.kz, 'top': self.kz,
            'left': self.kx, 'right': self.kx,
            'front': self.ky, 'back': self.ky,
        }
        for bname, bc in self.boundary_conditions.items():
            cells_b = self.mesh['boundary_cells'][bname]
            areas_b = self.mesh['boundary_areas'][bname]
            half_d = self.mesh['boundary_half_dist'][bname]
            k_n = normal_k[bname]
            btype = str(bc.get('type', 'convection')).lower().strip()

            if btype in ('convection', 'robin'):
                G_b = compute_boundary_conductance(k_n, float(bc['h']), half_d, areas_b, self.exact_boundary_series)
                np.add.at(diag_K_1d, cells_b, G_b)
                np.add.at(F_b, cells_b, G_b * float(bc['T_inf']))
            elif btype in ('temperature', 'dirichlet', 'constant_temperature'):
                T_w = float(bc['T_wall'] if 'T_wall' in bc else bc['T'])
                G_b = compute_dirichlet_conductance(k_n, half_d, areas_b)
                np.add.at(diag_K_1d, cells_b, G_b)
                np.add.at(F_b, cells_b, G_b * T_w)
            elif btype in ('heat_flux', 'neumann'):
                q_flux = float(bc.get('q_flux', bc.get('q', 0.0)))
                np.add.at(F_b, cells_b, q_flux * areas_b)
            elif btype in ('adiabatic', 'insulated'):
                pass
            else:
                raise ValueError(f"Unsupported boundary condition type '{btype}' for '{bname}'.")

        self.diag_K_3d = diag_K_1d.reshape((self.nx, self.ny, self.nz))
        self.F_b = F_b
        self._cached_lu = None
        self._cached_lu_key = None
        if self.solver_mode == 'scipy_direct':
            self._build_scipy_matrices(diag_K_1d)

    def _build_scipy_matrices(self, diag_K_1d: np.ndarray):
        """Assemble CSR matrices M and K for optional SciPy sparse direct solver."""
        cg = self.mesh['cell_grid']
        rows_list, cols_list, data_list = [], [], []
        if self.Gx is not None:
            p = cg[:-1, :, :].ravel()
            e = cg[1:, :, :].ravel()
            g = self.Gx.ravel()
            rows_list.extend([p, e])
            cols_list.extend([e, p])
            data_list.extend([-g, -g])
        if self.Gy is not None:
            p = cg[:, :-1, :].ravel()
            n = cg[:, 1:, :].ravel()
            g = self.Gy.ravel()
            rows_list.extend([p, n])
            cols_list.extend([n, p])
            data_list.extend([-g, -g])
        if self.Gz is not None:
            p = cg[:, :, :-1].ravel()
            t = cg[:, :, 1:].ravel()
            g = self.Gz.ravel()
            rows_list.extend([p, t])
            cols_list.extend([t, p])
            data_list.extend([-g, -g])

        idx_diag = np.arange(self.n_cells, dtype=int)
        all_rows = np.concatenate(rows_list + [idx_diag])
        all_cols = np.concatenate(cols_list + [idx_diag])
        all_data = np.concatenate(data_list + [diag_K_1d])
        self._K_sparse = csr_matrix((all_data, (all_rows, all_cols)), shape=(self.n_cells, self.n_cells))
        self._M_sparse = sp_diags(self.M_diag_1d, format='csr')

    def apply_K_3d(self, T3: np.ndarray) -> np.ndarray:
        """Matrix-free 7-point application of K to 3D field T3."""
        out = self.diag_K_3d * T3
        if self.Gx is not None:
            out[:-1, :, :] -= self.Gx * T3[1:, :, :]
            out[1:, :, :]  -= self.Gx * T3[:-1, :, :]
        if self.Gy is not None:
            out[:, :-1, :] -= self.Gy * T3[:, 1:, :]
            out[:, 1:, :]  -= self.Gy * T3[:, :-1, :]
        if self.Gz is not None:
            out[:, :, :-1] -= self.Gz * T3[:, :, 1:]
            out[:, :, 1:]  -= self.Gz * T3[:, :, :-1]
        return out

    def step_theta(self, T_prev: np.ndarray, qh_n: float, dt: float,
                   theta: float = 0.5, qh_np1: Optional[float] = None) -> np.ndarray:
        """
        Advance temperature field by dt using the theta-method:
          (M + theta * dt * K) T^{n+1} = (M - (1 - theta) * dt * K) T^n + dt * [(1-theta) F^n + theta F^{n+1}]
        """
        if dt <= 0.0:
            return T_prev.copy()

        qh_eff = qh_n if qh_np1 is None else ((1.0 - theta) * qh_n + theta * qh_np1)
        F_eff_1d = qh_eff * self.F_unit + self.F_b

        if self.n_cells == 1:
            # Exact scalar formula for 1x1x1 cell
            m = float(self.M_diag_1d[0])
            k = float(self.diag_K_3d[0, 0, 0])
            rhs = (m - (1.0 - theta) * dt * k) * T_prev[0] + dt * F_eff_1d[0]
            return np.array([rhs / (m + theta * dt * k)], dtype=np.float64)

        if self.solver_mode == 'scipy_direct' and HAS_SCIPY_SPARSE:
            b = dt * F_eff_1d + self.M_diag_1d * T_prev - (1.0 - theta) * dt * (self._K_sparse @ T_prev)
            lu_key = (round(dt, 9), round(theta, 6))
            if self._cached_lu is not None and self._cached_lu_key == lu_key:
                return self._cached_lu(b)
            A = (self._M_sparse + (theta * dt) * self._K_sparse).tocsc()
            try:
                lu = sp_factorized(A)
                self._cached_lu = lu
                self._cached_lu_key = lu_key
                return lu(b)
            except Exception:
                return sp_spsolve(A.tocsr(), b)

        # Pure NumPy matrix-free Jacobi-PCG
        T_prev_3d = T_prev.reshape((self.nx, self.ny, self.nz))
        KT_prev = self.apply_K_3d(T_prev_3d)
        b_3d = self.M_diag_3d * T_prev_3d - ((1.0 - theta) * dt) * KT_prev + dt * F_eff_1d.reshape((self.nx, self.ny, self.nz))

        alpha_k = theta * dt
        if alpha_k <= 0.0:
            # Explicit Euler
            return (b_3d / self.M_diag_3d).ravel()

        M_inv = 1.0 / (self.M_diag_3d + alpha_k * self.diag_K_3d)
        x = T_prev_3d.copy()
        Ax = self.M_diag_3d * x + alpha_k * self.apply_K_3d(x)
        r = b_3d - Ax
        b_norm = float(np.sqrt(np.sum(b_3d * b_3d)))
        tol_abs = self.pcg_tol * max(b_norm, 1.0)
        if float(np.sqrt(np.sum(r * r))) <= tol_abs:
            return x.ravel()

        z = M_inv * r
        p = z.copy()
        rz_old = float(np.sum(r * z))

        for _ in range(self.pcg_max_iter):
            Ap = self.M_diag_3d * p + alpha_k * self.apply_K_3d(p)
            pAp = float(np.sum(p * Ap))
            if pAp <= 1e-30:
                break
            alpha = rz_old / pAp
            x += alpha * p
            r -= alpha * Ap
            if float(np.sqrt(np.sum(r * r))) <= tol_abs:
                break
            z = M_inv * r
            rz_new = float(np.sum(r * z))
            beta = rz_new / max(rz_old, 1e-30)
            p = z + beta * p
            rz_old = rz_new

        return x.ravel()


def adaptive_time_step(T_prev: np.ndarray, T_current: np.ndarray, dt: float,
                       max_error: float = 0.1, min_dt: float = 0.1, max_dt: float = 20.0) -> float:
    temp_change = float(np.max(np.abs(T_current - T_prev)))
    avg_temp = float(np.mean(np.abs(T_prev)))
    error = (temp_change / avg_temp) if avg_temp > 0.0 else temp_change
    if error > max_error:
        return max(min_dt, dt * 0.5)
    elif error < max_error * 0.5:
        return min(max_dt, dt * 1.5)
    return dt


# ==============================================================================
# 3. 3D FVM Coupled Thermal Stepper (Plugs into DataPlot_hgp_engine)
# ==============================================================================

@dataclass
class FVM3DConfig:
    """Configuration for 3D FVM thermal model."""
    lx: float = 0.160
    ly: float = 0.020
    lz: float = 0.200
    nx: int = 20
    ny: int = 10
    nz: int = 20
    kx: float = 25.0
    ky: float = 0.99
    kz: float = 25.0
    rho: float = 2500.0
    cp: float = 1080.0
    theta: float = 0.5
    dt_th: float = 5.0
    exact_boundary_series: bool = True
    solver_mode: str = 'numpy_pcg'   # 'numpy_pcg' or 'scipy_direct'
    feedback_sensor: str = 'T_mean'  # 'T_mean', 'T_max', 'T_core', 'T_bottom_avg'
    cooling_sensor: str = 'T_max'    # 'T_max', 'T_mean', 'T_bottom_avg'
    bottom_liquid_hysteresis: bool = False
    t_cool_on: float = 35.0
    t_cool_off: float = 30.0
    h_liquid_on: float = 300.0
    h_liquid_off: float = 10.0
    t_liquid: float = 20.0
    t_amb: float = 25.0
    t_init: float = 25.0
    coeff_temp: float = 1.0
    snapshot_interval: float = 60.0
    max_snapshots: int = 60
    boundary_conditions: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        'bottom': {'type': 'convection', 'h': 300.0, 'T_inf': 25.0},
        'top':    {'type': 'convection', 'h': 5.0,   'T_inf': 25.0},
        'left':   {'type': 'convection', 'h': 5.0,   'T_inf': 25.0},
        'right':  {'type': 'convection', 'h': 5.0,   'T_inf': 25.0},
        'front':  {'type': 'convection', 'h': 5.0,   'T_inf': 25.0},
        'back':   {'type': 'convection', 'h': 5.0,   'T_inf': 25.0},
    })


class FVM3DThermalStepper:
    """
    Bidirectionally coupled 3D FVM thermal stepper for DataPlot_hgp_engine.
    Accumulates electrical heat Q_tot over macro thermal step dt_th, advances 3D FVM,
    handles optional bottom liquid cooling hysteresis, and feeds back selected T sensor.
    """

    def __init__(self, fvm_cfg: FVM3DConfig):
        self.cfg = fvm_cfg
        self.mesh = generate_fvm_mesh(
            (0.0, fvm_cfg.lx), (0.0, fvm_cfg.ly), (0.0, fvm_cfg.lz),
            fvm_cfg.nx, fvm_cfg.ny, fvm_cfg.nz
        )
        self.domain_volume = float(np.sum(self.mesh['cell_volumes']))
        self.weights = self.mesh['cell_volumes'] / self.domain_volume
        self.material_props = {
            'kx': fvm_cfg.kx, 'ky': fvm_cfg.ky, 'kz': fvm_cfg.kz,
            'rho': fvm_cfg.rho, 'cp': fvm_cfg.cp,
        }
        self.base_bcs = {k: dict(v) for k, v in fvm_cfg.boundary_conditions.items()}
        self.T_field = np.full(self.mesh['n_cells'], float(fvm_cfg.t_init), dtype=np.float64)
        self.cooling_on = False

        if fvm_cfg.bottom_liquid_hysteresis:
            self.cooling_on = (float(fvm_cfg.t_init) >= fvm_cfg.t_cool_on)
            self._apply_bottom_hysteresis_bc(self.base_bcs, self.cooling_on)

        self.operator = FVM3DOperator(
            self.mesh, fvm_cfg.kx, fvm_cfg.ky, fvm_cfg.kz,
            fvm_cfg.rho, fvm_cfg.cp, self.base_bcs,
            exact_boundary_series=fvm_cfg.exact_boundary_series,
            solver_mode=fvm_cfg.solver_mode,
        )

        # Macro-step accumulators
        self._dt_accum = 0.0
        self._q_energy_accum = 0.0
        self._last_t = 0.0
        self._last_snap_t = 0.0

        # History storage
        self.t_history: List[float] = [0.0]
        self.stats: Dict[str, List[float]] = {
            'min': [float(fvm_cfg.t_init)],
            'max': [float(fvm_cfg.t_init)],
            'avg': [float(fvm_cfg.t_init)],
            'std': [0.0],
        }
        self.q_history: List[float] = [0.0]
        self.cool_history: List[int] = [1 if self.cooling_on else 0]
        self.t_snapshots: List[float] = [0.0]
        self.T_snapshots: List[np.ndarray] = [self.T_field.copy()]

    def _apply_bottom_hysteresis_bc(self, bcs: Dict[str, Dict[str, Any]], cool_on: bool):
        bcs['bottom'] = {
            'type': 'convection',
            'h': float(self.cfg.h_liquid_on if cool_on else self.cfg.h_liquid_off),
            'T_inf': float(self.cfg.t_liquid if cool_on else self.cfg.t_amb),
        }

    def _read_sensor(self, sensor: str) -> float:
        s = str(sensor).strip()
        if s == 'T_max':
            return float(np.max(self.T_field))
        if s == 'T_core':
            cg = self.mesh['cell_grid']
            idx = cg[self.mesh['nx'] // 2, self.mesh['ny'] // 2, self.mesh['nz'] // 2]
            return float(self.T_field[idx])
        if s == 'T_bottom_avg':
            cells = self.mesh['boundary_cells']['bottom']
            return float(np.mean(self.T_field[cells]))
        return float(np.sum(self.weights * self.T_field))

    def reset_temperature(self, T_val: float, T_amb_val: Optional[float] = None):
        self.T_field.fill(float(T_val))
        if T_amb_val is not None:
            self.cfg.t_amb = float(T_amb_val)
        if self.cfg.bottom_liquid_hysteresis:
            self.cooling_on = (float(T_val) >= self.cfg.t_cool_on)
            self._apply_bottom_hysteresis_bc(self.base_bcs, self.cooling_on)
            self.operator.update_boundaries(self.base_bcs)
        self.stats['min'][0] = float(T_val)
        self.stats['max'][0] = float(T_val)
        self.stats['avg'][0] = float(T_val)
        self.T_snapshots[0] = self.T_field.copy()

    def _advance_fvm(self, q_avg_W: float, dt_macro: float, t_now: float):
        if self.cfg.bottom_liquid_hysteresis:
            T_ctrl = self._read_sensor(self.cfg.cooling_sensor)
            new_cool = self.cooling_on
            if T_ctrl >= self.cfg.t_cool_on:
                new_cool = True
            elif T_ctrl <= self.cfg.t_cool_off:
                new_cool = False
            if new_cool != self.cooling_on:
                self.cooling_on = new_cool
                self._apply_bottom_hysteresis_bc(self.base_bcs, self.cooling_on)
                self.operator.update_boundaries(self.base_bcs)

        qh_vol = (self.cfg.coeff_temp * q_avg_W) / self.domain_volume
        self.T_field = self.operator.step_theta(self.T_field, qh_vol, dt_macro, theta=self.cfg.theta)

        t_min = float(np.min(self.T_field))
        t_max = float(np.max(self.T_field))
        t_avg = float(np.sum(self.weights * self.T_field))
        t_std = float(np.sqrt(np.sum(self.weights * (self.T_field - t_avg) ** 2)))

        self.t_history.append(float(t_now))
        self.stats['min'].append(t_min)
        self.stats['max'].append(t_max)
        self.stats['avg'].append(t_avg)
        self.stats['std'].append(t_std)
        self.q_history.append(float(q_avg_W))
        self.cool_history.append(1 if self.cooling_on else 0)

        if (t_now - self._last_snap_t) >= self.cfg.snapshot_interval - 1e-9:
            self._last_snap_t = float(t_now)
            self.t_snapshots.append(float(t_now))
            self.T_snapshots.append(self.T_field.copy())
            if len(self.T_snapshots) > self.cfg.max_snapshots:
                # Keep first, last, and evenly thinned interior snapshots
                keep_idx = np.linspace(0, len(self.T_snapshots) - 1, self.cfg.max_snapshots, dtype=int)
                keep_idx = sorted(set(keep_idx.tolist()))
                self.t_snapshots = [self.t_snapshots[i] for i in keep_idx]
                self.T_snapshots = [self.T_snapshots[i] for i in keep_idx]

    def step(self, q_tot: float, dt: float, t_now: float) -> Tuple[float, bool]:
        if dt > 0.0:
            self._q_energy_accum += float(q_tot) * float(dt)
            self._dt_accum += float(dt)
            self._last_t = float(t_now)
            if self._dt_accum >= self.cfg.dt_th - 1e-9:
                q_avg = self._q_energy_accum / self._dt_accum
                self._advance_fvm(q_avg, self._dt_accum, t_now)
                self._dt_accum = 0.0
                self._q_energy_accum = 0.0
        return self._read_sensor(self.cfg.feedback_sensor), self.cooling_on

    def finalize(self) -> Dict[str, Any]:
        # Flush any remaining accumulated sub-macro step
        if self._dt_accum > 1e-9:
            q_avg = self._q_energy_accum / self._dt_accum
            self._advance_fvm(q_avg, self._dt_accum, self._last_t)
            self._dt_accum = 0.0
            self._q_energy_accum = 0.0
        # Ensure final frame is always in snapshots
        if len(self.t_snapshots) == 0 or abs(self.t_snapshots[-1] - self.t_history[-1]) > 1e-6:
            self.t_snapshots.append(float(self.t_history[-1]))
            self.T_snapshots.append(self.T_field.copy())

        return {
            'thermal_dim': '3D',
            'mesh': self.mesh,
            'material_props': dict(self.material_props),
            'boundary_conditions': {k: dict(v) for k, v in self.base_bcs.items()},
            'time_history': list(self.t_history),
            'stats': self.stats,
            'q_history': list(self.q_history),
            'cool_history': list(self.cool_history),
            't_snapshots': list(self.t_snapshots),
            'T_snapshots': self.T_snapshots,
            'T_final': self.T_field.copy(),
        }


# ==============================================================================
# 4. Standalone 3D FVM Simulation (External Power Table / Constant / Function)
# ==============================================================================

def load_power_table(file_path: str, sheet_name: str = 'Sheet1',
                     time_col: str = 'Time', power_col: str = 'Power') -> Tuple[np.ndarray, np.ndarray]:
    if file_path.lower().endswith(('.xlsx', '.xls')):
        df = pd.read_excel(file_path, sheet_name=sheet_name)
    else:
        df = pd.read_csv(file_path)
    t_arr = df[time_col].to_numpy(dtype=np.float64)
    p_arr = df[power_col].to_numpy(dtype=np.float64)
    mask = np.isfinite(t_arr) & np.isfinite(p_arr)
    t_arr, p_arr = t_arr[mask], p_arr[mask]
    order = np.argsort(t_arr)
    return t_arr[order], p_arr[order]


def run_fvm3d_standalone(
    fvm_cfg: FVM3DConfig,
    total_time: float,
    qh_type: str = 'constant',
    qh_const_W: float = 120.0,
    power_table: Optional[Tuple[np.ndarray, np.ndarray]] = None,
    adaptive_dt: bool = False,
    dt_initial: float = 1.0,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Run standalone 3D FVM transient heat conduction (no electrochemical model).
    Supports qh_type in ('constant', 'table').
    Pure function: never mutates input dicts.
    """
    t0 = time.perf_counter()
    mesh = generate_fvm_mesh(
        (0.0, fvm_cfg.lx), (0.0, fvm_cfg.ly), (0.0, fvm_cfg.lz),
        fvm_cfg.nx, fvm_cfg.ny, fvm_cfg.nz
    )
    domain_vol = float(np.sum(mesh['cell_volumes']))
    weights = mesh['cell_volumes'] / domain_vol
    mat_props = {
        'kx': fvm_cfg.kx, 'ky': fvm_cfg.ky, 'kz': fvm_cfg.kz,
        'rho': fvm_cfg.rho, 'cp': fvm_cfg.cp,
    }
    bcs = {k: dict(v) for k, v in fvm_cfg.boundary_conditions.items()}

    if progress_cb:
        progress_cb(
            f"[3D FVM] 网格 {fvm_cfg.nx}x{fvm_cfg.ny}x{fvm_cfg.nz} ({mesh['n_cells']} 单元), "
            f"求解器={fvm_cfg.solver_mode}, 热源={qh_type}, 总时长={total_time:.1f}s"
        )

    op = FVM3DOperator(
        mesh, fvm_cfg.kx, fvm_cfg.ky, fvm_cfg.kz,
        fvm_cfg.rho, fvm_cfg.cp, bcs,
        exact_boundary_series=fvm_cfg.exact_boundary_series,
        solver_mode=fvm_cfg.solver_mode,
    )

    def eval_power_W(t_val: float) -> float:
        if qh_type == 'table' and power_table is not None:
            t_tab, p_tab = power_table
            # Match scipy interp1d linear extrapolation behavior
            if t_val <= t_tab[0]:
                if len(t_tab) >= 2 and t_tab[1] != t_tab[0]:
                    return float(p_tab[0] + (p_tab[1] - p_tab[0]) * (t_val - t_tab[0]) / (t_tab[1] - t_tab[0]))
                return float(p_tab[0])
            if t_val >= t_tab[-1]:
                if len(t_tab) >= 2 and t_tab[-1] != t_tab[-2]:
                    return float(p_tab[-1] + (p_tab[-1] - p_tab[-2]) * (t_val - t_tab[-1]) / (t_tab[-1] - t_tab[-2]))
                return float(p_tab[-1])
            return float(np.interp(t_val, t_tab, p_tab))
        return float(qh_const_W)

    T_cur = np.full(mesh['n_cells'], float(fvm_cfg.t_init), dtype=np.float64)
    t_cur = 0.0
    dt = float(dt_initial if adaptive_dt else fvm_cfg.dt_th)

    time_history = [0.0]
    q_history = [eval_power_W(0.0)]
    stats = {
        'min': [float(fvm_cfg.t_init)],
        'max': [float(fvm_cfg.t_init)],
        'avg': [float(fvm_cfg.t_init)],
        'std': [0.0],
    }
    t_snapshots = [0.0]
    T_snapshots = [T_cur.copy()]
    last_snap_t = 0.0
    step_cnt = 0

    while t_cur < total_time - 1e-12:
        if cancel_check and cancel_check():
            if progress_cb:
                progress_cb("[3D FVM] 用户中止求解。")
            break
        cur_dt = min(dt, total_time - t_cur)
        t_next = t_cur + cur_dt
        q_n = eval_power_W(t_cur)
        q_np1 = eval_power_W(t_next) if qh_type == 'table' else q_n
        T_new = op.step_theta(
            T_cur,
            (fvm_cfg.coeff_temp * q_n) / domain_vol,
            cur_dt,
            theta=fvm_cfg.theta,
            qh_np1=(fvm_cfg.coeff_temp * q_np1) / domain_vol if qh_type == 'table' else None,
        )
        if adaptive_dt:
            dt = adaptive_time_step(T_cur, T_new, dt)
        T_cur = T_new
        t_cur = t_next
        step_cnt += 1

        t_min = float(np.min(T_cur))
        t_max = float(np.max(T_cur))
        t_avg = float(np.sum(weights * T_cur))
        t_std = float(np.sqrt(np.sum(weights * (T_cur - t_avg) ** 2)))
        time_history.append(float(t_cur))
        q_history.append(float(q_np1))
        stats['min'].append(t_min)
        stats['max'].append(t_max)
        stats['avg'].append(t_avg)
        stats['std'].append(t_std)

        if (t_cur - last_snap_t) >= fvm_cfg.snapshot_interval - 1e-9 or t_cur >= total_time - 1e-9:
            last_snap_t = float(t_cur)
            t_snapshots.append(float(t_cur))
            T_snapshots.append(T_cur.copy())
            if len(T_snapshots) > fvm_cfg.max_snapshots:
                keep_idx = sorted(set(np.linspace(0, len(T_snapshots) - 1, fvm_cfg.max_snapshots, dtype=int).tolist()))
                t_snapshots = [t_snapshots[i] for i in keep_idx]
                T_snapshots = [T_snapshots[i] for i in keep_idx]

        if progress_cb and (step_cnt == 1 or step_cnt % 10 == 0 or t_cur >= total_time - 1e-9):
            progress_cb(
                f"  [3D FVM 步 {step_cnt}] t={t_cur:.1f}/{total_time:.1f}s | "
                f"T_min={t_min:.2f}, T_avg={t_avg:.2f}, T_max={t_max:.2f} C | Q={q_np1:.1f} W"
            )

    elapsed = time.perf_counter() - t0
    if progress_cb:
        progress_cb(f"[3D FVM] 求解完成，共 {step_cnt} 步，耗时 {elapsed:.2f} s。")

    stats_df = pd.DataFrame({
        'time': time_history,
        'Power_W': q_history,
        'T_min_degC': stats['min'],
        'T_avg_degC': stats['avg'],
        'T_max_degC': stats['max'],
        'T_std_degC': stats['std'],
        'DeltaT_degC': np.array(stats['max']) - np.array(stats['min']),
    })

    return {
        'mode': 'fvm3d_standalone',
        'out_df': stats_df,
        'summary_df': pd.DataFrame([{
            'Mode': f'3D_FVM_{qh_type}',
            'Duration_s': float(time_history[-1]),
            'Steps': step_cnt,
            'Init_Avg_T_degC': stats['avg'][0],
            'Final_Avg_T_degC': stats['avg'][-1],
            'Max_T_degC': float(max(stats['max'])),
            'Min_T_degC': float(min(stats['min'])),
            'Final_DeltaT_degC': stats['max'][-1] - stats['min'][-1],
            'Elapsed_s': elapsed,
        }]),
        'elapsed': elapsed,
        'thermal': {
            'thermal_dim': '3D',
            'mesh': mesh,
            'material_props': mat_props,
            'boundary_conditions': bcs,
            'time_history': time_history,
            'stats': stats,
            'q_history': q_history,
            'cool_history': [0] * len(time_history),
            't_snapshots': t_snapshots,
            'T_snapshots': T_snapshots,
            'T_final': T_cur.copy(),
        },
    }
