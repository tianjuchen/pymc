#   Copyright 2021 The PyMC Developers
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
import aesara
import aesara.tensor as at
import numpy as np
import pytest
import scipy.stats.distributions as sp

from aesara.graph.basic import Constant, graph_inputs
from aesara.graph.fg import FunctionGraph
from aesara.tensor.random.op import RandomVariable
from aesara.tensor.subtensor import (
    AdvancedIncSubtensor,
    AdvancedIncSubtensor1,
    IncSubtensor,
)

from pymc3.aesaraf import floatX, walk_model
from pymc3.distributions.continuous import Normal, Uniform
from pymc3.distributions.logp import logpt
from pymc3.model import Model


def test_logpt_basic():
    """Make sure we can compute a log-likelihood for a hierarchical model with transforms."""

    with Model() as m:
        a = Uniform("a", 0.0, 1.0)
        c = Normal("c")
        b_l = c * a + 2.0
        b = Uniform("b", b_l, b_l + 1.0)

    a_value_var = m.rvs_to_values[a]
    assert a_value_var.tag.transform

    b_value_var = m.rvs_to_values[b]
    assert b_value_var.tag.transform

    c_value_var = m.rvs_to_values[c]

    b_logp = logpt(b, b_value_var)

    res_ancestors = list(walk_model((b_logp,), walk_past_rvs=True))
    res_rv_ancestors = [
        v for v in res_ancestors if v.owner and isinstance(v.owner.op, RandomVariable)
    ]

    # There shouldn't be any `RandomVariable`s in the resulting graph
    assert len(res_rv_ancestors) == 0
    assert b_value_var in res_ancestors
    assert c_value_var in res_ancestors
    assert a_value_var in res_ancestors


@pytest.mark.parametrize(
    "indices, size",
    [
        (slice(0, 2), 5),
        (np.r_[True, True, False, False, True], 5),
        (np.r_[0, 1, 4], 5),
        ((np.array([0, 1, 4]), np.array([0, 1, 4])), (5, 5)),
    ],
)
def test_logpt_univariate_incsubtensor(indices, size):
    """Make sure we can compute a log-likelihood for ``Y[idx] = data`` where ``Y`` is univariate."""

    mu = floatX(np.power(10, np.arange(np.prod(size)))).reshape(size)
    data = mu[indices]
    sigma = 0.001
    rng = aesara.shared(np.random.RandomState(232), borrow=True)

    with Model() as m:
        a = Normal("a", mu, sigma, size=size, rng=rng)

    a_idx = at.set_subtensor(a[indices], data)

    assert isinstance(a_idx.owner.op, (IncSubtensor, AdvancedIncSubtensor, AdvancedIncSubtensor1))

    a_idx_value_var = a_idx.type()
    a_idx_value_var.name = "a_idx_value"

    a_idx_logp = logpt(a_idx, a_idx_value_var)

    logp_vals = a_idx_logp.eval()

    # The indices that were set should all have the same log-likelihood values,
    # because the values they were set to correspond to the unique means along
    # that dimension.  This helps us confirm that the log-likelihood is
    # associating the assigned values with their correct parameters.
    exp_obs_logps = sp.norm.logpdf(mu, mu, sigma)[indices]
    np.testing.assert_almost_equal(logp_vals[indices], exp_obs_logps)

    # Next, we need to confirm that the unset indices are being sampled
    # from the original random variable in the correct locations.
    # rng.get_value(borrow=True).seed(232)

    res_ancestors = list(walk_model((a_idx_logp,), walk_past_rvs=True))
    res_rv_ancestors = tuple(
        v for v in res_ancestors if v.owner and isinstance(v.owner.op, RandomVariable)
    )

    assert res_rv_ancestors == (a,)

    fg = FunctionGraph(
        [v for v in graph_inputs((a_idx_logp,)) if not isinstance(v, Constant)],
        [a_idx_logp],
        clone=False,
    )

    ((a_client, _),) = fg.clients[a]

    assert isinstance(a_client.op, (IncSubtensor, AdvancedIncSubtensor, AdvancedIncSubtensor1))
    indices = tuple(i.eval() for i in a_client.inputs[2:])
    np.testing.assert_almost_equal(indices, indices)