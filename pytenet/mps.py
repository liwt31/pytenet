import numpy as np
from .qnumber import qnumber_outer_sum, qnumber_flatten, is_qsparse
from .bond_ops import qr, split_matrix_svd

__all__ = ['MPS', 'merge_MPS_tensor_pair', 'split_MPS_tensor']


class MPS(object):
    """
    Matrix product state (MPS) class.

    The i-th MPS tensor has dimension `[d, D[i], D[i+1]]` with `d` the physical
    dimension at each site and `D` the list of virtual bond dimensions.

    Quantum numbers are assumed to be additive and stored as integers.
    `qd` stores the list of physical quantum numbers at each site,
    and `qD` the virtual bond quantum numbers.
    The sum of physical and left virtual bond quantum number of each non-zero
    tensor entry must be equal to the right virtual bond quantum number.
    """

    def __init__(self, qd, qD, fill=0.0):
        """
        Create a matrix product state.

        Args:
            qd: physical quantum numbers at each site (same for all sites)
            qD: virtual bond quantum numbers (list of quantum number lists)
            fill: explicit scalar number to fill MPS tensors with, or
                  'random' to initialize tensors with random complex entries
        """
        # require NumPy arrays
        self.qd = np.array(qd)
        self.qD = [np.array(qDi) for qDi in qD]
        # create list of MPS tensors
        d = len(qd)
        D = [len(qb) for qb in qD]
        # leading and trailing bond dimensions must be 1
        assert D[0] == 1 and D[-1] == 1
        if isinstance(fill, int) or isinstance(fill, float) or isinstance(fill, complex):
            self.A = [np.full((d, D[i], D[i+1]), fill) for i in range(len(D)-1)]
        elif fill == 'random':
            # random complex entries
            self.A = [
                    np.random.normal(size=(d, D[i], D[i+1]), scale=1./np.sqrt(d*D[i]*D[i+1])) +
                 1j*np.random.normal(size=(d, D[i], D[i+1]), scale=1./np.sqrt(d*D[i]*D[i+1])) for i in range(len(D)-1)]
        else:
            raise ValueError('fill = {} invalid; must be a number or "random".'.format(fill))
        # enforce block sparsity structure dictated by quantum numbers
        for i in range(len(self.A)):
            mask = qnumber_outer_sum([self.qd, self.qD[i], -self.qD[i+1]])
            self.A[i] = np.where(mask == 0, self.A[i], 0)

    @property
    def nsites(self):
        """Number of lattice sites."""
        return len(self.A)

    @property
    def bond_dims(self):
        """Virtual bond dimensions."""
        if len(self.A) == 0:
            return []
        else:
            D = [self.A[i].shape[1] for i in range(len(self.A))]
            D.append(self.A[-1].shape[2])
            return D

    def zero_qnumbers(self):
        """Set all quantum numbers to zero (effectively disabling them)."""
        self.qd.fill(0)
        for i in range(len(self.qD)):
            self.qD[i].fill(0)

    def orthonormalize(self, mode='left'):
        """Left- or right-orthonormalize the MPS using QR decompositions."""
        if len(self.A) == 0:
            return

        if mode == 'left':
            for i in range(len(self.A) - 1):
                self.A[i], self.A[i+1], self.qD[i+1] = local_orthonormalize_left_qr(self.A[i], self.A[i+1], self.qd, self.qD[i:i+2])
            # last tensor
            self.A[-1], T, self.qD[-1] = local_orthonormalize_left_qr(self.A[-1], np.array([[[1]]]), self.qd, self.qD[-2:])
            # normalization factor (real-valued since diagonal of R matrix is real)
            assert T.shape == (1, 1, 1)
            nrm = T[0, 0, 0].real
            if nrm < 0:
                # flip sign such that normalization factor is always non-negative
                self.A[-1] = -self.A[-1]
                nrm = -nrm
            return nrm
        elif mode == 'right':
            for i in reversed(range(1, len(self.A))):
                self.A[i], self.A[i-1], self.qD[i] = local_orthonormalize_right_qr(self.A[i], self.A[i-1], self.qd, self.qD[i:i+2])
            # first tensor
            self.A[0], T, self.qD[0] = local_orthonormalize_right_qr(self.A[0], np.array([[[1]]]), self.qd, self.qD[:2])
            # normalization factor (real-valued since diagonal of R matrix is real)
            assert T.shape == (1, 1, 1)
            nrm = T[0, 0, 0].real
            if nrm < 0:
                # flip sign such that normalization factor is always non-negative
                self.A[0] = -self.A[0]
                nrm = -nrm
            return nrm
        else:
            raise ValueError('mode = {} invalid; must be "left" or "right".'.format(mode))

    def as_vector(self):
        """Merge all tensors to obtain the vector representation on the full Hilbert space."""
        psi = self.A[0]
        for i in range(1, len(self.A)):
            psi = merge_MPS_tensor_pair(psi, self.A[i])
        assert psi.ndim == 3
        assert psi.shape[1] == 1 and psi.shape[2] == 1
        return psi.reshape(-1)

    def __add__(self, other):
        """Add MPS to another."""
        return add_MPS(self, other)


def local_orthonormalize_left_qr(A, Anext, qd, qD):
    """
    Left-orthonormalize local site tensor `A` by a QR decomposition,
    and update tensor at next site.
    """
    # perform QR decomposition and replace A by reshaped Q matrix
    s = A.shape
    assert len(s) == 3
    q0 = qnumber_flatten([qd, qD[0]])
    (Q, R, qbond) = qr(A.reshape((s[0]*s[1], s[2])), q0, qD[1])
    A = Q.reshape((s[0], s[1], Q.shape[1]))
    # update Anext tensor: multiply with R from left
    Anext = np.tensordot(R, Anext, (1, 1)).transpose((1, 0, 2))
    return (A, Anext, qbond)


def local_orthonormalize_right_qr(A, Aprev, qd, qD):
    """
    Right-orthonormalize local site tensor `A` by a QR decomposition,
    and update tensor at previous site.
    """
    # flip left and right virtual bond dimensions
    A = A.transpose((0, 2, 1))
    # perform QR decomposition and replace A by reshaped Q matrix
    s = A.shape
    assert len(s) == 3
    q0 = qnumber_flatten([qd, -qD[1]])
    (Q, R, qbond) = qr(A.reshape((s[0]*s[1], s[2])), q0, -qD[0])
    A = Q.reshape((s[0], s[1], Q.shape[1])).transpose((0, 2, 1))
    # update Aprev tensor: multiply with R from right
    Aprev = np.tensordot(Aprev, R, (2, 1))
    return (A, Aprev, -qbond)


def merge_MPS_tensor_pair(A0, A1):
    """Merge two neighboring MPS tensors."""
    A = np.tensordot(A0, A1, (2, 1))
    # pair original physical dimensions of A0 and A1
    A = A.transpose((0, 2, 1, 3))
    # combine original physical dimensions
    A = A.reshape((A.shape[0]*A.shape[1], A.shape[2], A.shape[3]))
    return A


def split_MPS_tensor(A, qd0, qd1, qD, svd_distr, tol=0):
    """
    Split a MPS tensor with dimension `d0*d1 x D0 x D2` into two MPS tensors
    with dimensions `d0 x D0 x D1` and `d1 x D1 x D2`, respectively.
    """
    assert A.ndim == 3
    d0 = len(qd0)
    d1 = len(qd1)
    assert d0 * d1 == A.shape[0], 'physical dimension of MPS tensor must be equal to d0 * d1'
    # reshape as matrix and split by SVD
    A = A.reshape((d0, d1, A.shape[1], A.shape[2])).transpose((0, 2, 1, 3))
    s = A.shape
    q0 = qnumber_flatten([ qd0, qD[0]])
    q1 = qnumber_flatten([-qd1, qD[1]])
    A0, sigma, A1, qbond = split_matrix_svd(A.reshape((s[0]*s[1], s[2]*s[3])), q0, q1, tol)
    A0.shape = (s[0], s[1], len(sigma))
    A1.shape = (len(sigma), s[2], s[3])
    # use broadcasting to distribute singular values
    if svd_distr == 'left':
        A0 = A0 * sigma
    elif svd_distr == 'right':
        A1 = A1 * sigma[:, None, None]
    elif svd_distr == 'sqrt':
        s = np.sqrt(sigma)
        A0 = A0 * s
        A1 = A1 * s[:, None, None]
    else:
        raise ValueError('svd_distr parameter must be "left", "right" or "sqrt".')
    # move physical dimension to the front
    A1 = A1.transpose((1, 0, 2))
    return (A0, A1, qbond)


def add_MPS(mps0, mps1):
    """"Logical addition of two matrix product states (effectively sum virtual bond dimensions)."""
    # number of lattice sites must agree
    assert mps0.nsites == mps1.nsites
    L = mps0.nsites
    # physical quantum numbers must agree
    assert np.array_equal(mps0.qd, mps1.qd)
    d = len(mps0.qd)

    # initialize with dummy tensors and bond quantum numbers
    mps = MPS(mps0.qd, (L+1)*[[0]])

    if L == 1:
        # single site
        # dummy bond quantum numbers must agree
        assert np.array_equal(mps0.qD[0], mps1.qD[0])
        assert np.array_equal(mps0.qD[1], mps1.qD[1])
        mps.qD[0] = mps0.qD[0].copy()
        mps.qD[1] = mps0.qD[1].copy()
        # simply add MPS tensors
        mps.A[0] = mps0.A[0] + mps1.A[0]
        # consistency check
        assert is_qsparse(mps.A[0], [mps.qd, mps.qD[0], -mps.qD[1]]), \
            'sparsity pattern of MPS tensor does not match quantum numbers'
    elif L > 1:
        # combine virtual bond quantum numbers
        # leading and trailing (dummy) bond quantum numbers must agree
        assert np.array_equal(mps0.qD[ 0], mps1.qD[ 0])
        assert np.array_equal(mps0.qD[-1], mps1.qD[-1])
        mps.qD[ 0] = mps0.qD[ 0].copy()
        mps.qD[-1] = mps0.qD[-1].copy()
        # intermediate bond quantum numbers
        for i in range(1, L):
            mps.qD[i] = np.concatenate((mps0.qD[i], mps1.qD[i]))

        # leftmost tensor
        mps.A[0] = np.block([mps0.A[0], mps1.A[0]])
        # intermediate tensors
        for i in range(1, L - 1):
            s0 = mps0.A[i].shape
            s1 = mps1.A[i].shape
            # form block-diagonal tensor
            mps.A[i] = np.block([[mps0.A[i], np.zeros((d, s0[1], s1[2]))], [np.zeros((d, s1[1], s0[2])), mps1.A[i]]])
        # rightmost tensor
        mps.A[-1] = np.block([[mps0.A[-1]], [mps1.A[-1]]])

        # consistency check
        for i in range(1, L):
            assert is_qsparse(mps.A[i], [mps.qd, mps.qD[i], -mps.qD[i+1]]), \
                'sparsity pattern of MPS tensor does not match quantum numbers'
    return mps
