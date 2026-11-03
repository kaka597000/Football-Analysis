"""Finite differences of the optimizer's actual objective; no fit-quality claim."""
import unittest
import numpy as np
from scipy import sparse
from scipy.optimize._numdiff import approx_derivative
from models import _poisson_objective


class GradientTests(unittest.TestCase):
 def test_interior_and_both_saturated_regions(self):
  X=sparse.csr_matrix([[1.,0.,1.],[1.,1.,0.],[1.,-1.,.5]])
  y=np.array([0.,2.,4.]);w=np.array([1.,.7,.2]);penalty=np.array([.001,1.,3.])
  for b in (np.array([.2,.1,-.3]),np.array([6.,.1,.2]),np.array([-7.,.1,.2]),np.array([0.,7.,-7.])):
   with self.subTest(coefficients=b):
    fun=lambda value:_poisson_objective(value,X,y,w,penalty)[0]
    actual=_poisson_objective(b,X,y,w,penalty)[1]
    expected=approx_derivative(fun,b,method='3-point').ravel()
    np.testing.assert_allclose(actual,expected,rtol=1e-7,atol=2e-8)


if __name__=='__main__':unittest.main()
