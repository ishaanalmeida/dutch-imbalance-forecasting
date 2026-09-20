"""Force cvxpy to load before pandas.

osqp's native DLL segfaults on Windows when pandas is loaded first
(pandas loads BLAS/LAPACK DLLs that conflict with osqp's qdldl).
Importing cvxpy here — before any test module triggers a pandas import —
sidesteps the crash. We only use CLARABEL, so osqp is never called.
"""

import cvxpy as cp  # noqa: F401
