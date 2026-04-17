# ML L4 — Backpropagation and Gradient Descent

Backpropagation is the algorithm that computes gradients of a neural network's loss
with respect to its weights. It is a specific application of the chain rule from
calculus, executed efficiently by reusing intermediate computations.

## Forward pass

Given an input x, the network computes activations layer by layer. For layer l with
weights W_l and bias b_l, the pre-activation is z_l = W_l @ a_{l-1} + b_l, and the
activation is a_l = f(z_l) for some nonlinearity f (ReLU, sigmoid, GELU). The final
activation is compared to the target y via a loss function L.

## Backward pass

The gradient of L with respect to the output activation is computed first. Then,
for each layer l from last to first, we compute:

- dL/dz_l = dL/da_l * f'(z_l)    (chain rule through the activation)
- dL/dW_l = dL/dz_l * a_{l-1}^T   (chain rule through the linear layer)
- dL/da_{l-1} = W_l^T * dL/dz_l   (propagate back to previous layer)

The gradient dL/dW_l tells gradient descent how to update the weights.

## Gradient descent

Weights are updated by W_l := W_l - learning_rate * dL/dW_l. The learning rate
controls step size. Too high and training diverges; too low and it stalls.

## Common issues

- Vanishing gradients: with sigmoid activations, f'(z) is near zero for large |z|,
  so gradients shrink multiplicatively through deep networks. ReLU partly fixes this.
- Exploding gradients: the opposite problem; use gradient clipping or careful
  initialization (Xavier, He).
- Local minima and saddle points: SGD with momentum helps escape; Adam is the
  default adaptive optimizer in practice.

## Practical notes

Most frameworks (PyTorch, JAX) implement backprop via automatic differentiation,
so you do not need to derive gradients by hand. You just define the forward
computation; the framework records a computation graph and walks it backward.
