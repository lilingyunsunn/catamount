import os
import tensorflow as tf

# To import graphs that use TF contrib libraries...
import tensorflow.contrib.mpi_collectives as mpi

from cougr.graph import *
from cougr.ops.array_ops import *
from cougr.ops.constant import *
from cougr.ops.ctrl_ops import *
from cougr.ops.init_ops import *
from cougr.ops.math_ops import *
from cougr.ops.placeholder import *
from cougr.ops.unknown_op import *
from cougr.ops.variable import *
from cougr.tensors.tensor import *

# Tools to import Tensorflow MetaGraphs into CouGr format

TF_OP_TO_COUGR = {
    'Add': AddOp,
    'Assign': AssignOp,
    'AssignAdd': AddOp, # Here, TF reuses the input tensor for output
    'AssignSub': SubOp, # Here, TF reuses the input tensor for output
    'BiasAdd': AddOp, # Here, TF special-case for 1D bias input
    'Cast': CastOp,
    'ConcatV2': ConcatOp,
    'Const': ConstOp,
    'Conv2D': Conv2DOp,
    'Enter': EnterOp,
    'Exit': ExitOp,
    'Exp': ExpOp,
    'Fill': FillOp,
    'FloorDiv': BasePointwiseOp,
    'FloorMod': BasePointwiseOp,
    'Gather': GatherOp,
    'Identity': IdentityOp,
    'Less': LessOp,
    'LogicalAnd': LogicalAndOp,
    'LogicalNot': LogicalNotOp,
    'LoopCond': LoopConditionOp,
    'MatMul': MatMulOp,
    'Maximum': MaximumOp,
    'Mean': ReduceOp,
    'Merge': MergeOp,
    'Minimum': MinimumOp,
    # tf.contrib.mpi_collectives.MPIInit has no compute graph function
    'MPIInit': NoOp,
    # tf.contrib.mpi_collectives.MPISize behaves like a placeholder
    'MPISize': PlaceholderOp,
    'Mul': MulOp,
    'Neg': NegOp,
    'NextIteration': NextIterationOp,
    'NoOp': NoOp, # Ignore no-ops
    'NotEqual': NotEqualOp,
    'OnesLike': NumLikeOp,
    'Pack': StackOp,
    'Placeholder': PlaceholderOp,
    'Prod': ReduceOp,
    'Pow': PowOp,
    'RandomUniform': RandomInitializerOp,
    'RealDiv': BasePointwiseOp,
    'Relu': ReluOp,
    'Reduce': ReduceOp,
    'Reshape': ReshapeOp,
    'RestoreV2': NoOp, # Ignore Restore ops
    'Rsqrt': RsqrtOp,
    'SaveV2': NoOp, # Ignore Saver ops
    'Scatter': ScatterOp,
    'Shape': ShapeOp,
    'Sigmoid': SigmoidOp,
    'SplitV': SplitOp,
    'Sqrt': SqrtOp,
    'StridedSlice': StridedSliceOp,
    'Sub': SubOp,
    'Sum': ReduceOp,
    'Switch': SwitchOp,
    'Tanh': TanhOp,
    'Transpose': TransposeOp,
    'VariableV2': VariableOp,
    'ZerosLike': NumLikeOp,
}

# [_] TODO (Joel): Add these for ResNets!
# AddN
# ApplyMomentum
# BiasAddGrad
# BroadcastGradientArgs
# Conv2DBackpropFilter
# Conv2DBackpropInput
# DynamicStitch
# ExpandDims
# FIFOQueueV2
# FusedBatchNorm
# FusedBatchNormGrad
# InTopK
# MaxPool
# MaxPoolGrad
# MergeSummary
# PreventGradient
# QueueCloseV2
# QueueDequeueV2
# QueueEnqueueV2
# QueueSizeV2
# Range
# ReluGrad
# ScalarSummary
# SparseSoftmaxCrossEntropyWithLogits
# Stage
# Tile
# TruncatedNormal
# Unstage
# ZerosLike


TF_DTYPE_TO_COUGR = {
    tf.bool: DataType.bool,
    tf.int32: DataType.int32,
    tf.int64: DataType.int64,
    tf.uint8: DataType.uint32,
    tf.float32: DataType.float32,
    tf.string: DataType.string,
}

def tf_shape_to_cougr(tf_shape):
    dims = None
    if tf_shape is not None and tf_shape.ndims is not None:
        dims = []
        if tf_shape.dims is not None and len(tf_shape.dims) > 0:
            for dim in tf_shape.dims:
                dims.append(dim.value)
    return TensorShape(dims)

def load_tf_session(tf_filename):
    if '.meta' not in tf_filename or not os.path.exists(tf_filename):
        raise FileNotFoundError('ERROR: Invalid file {}. Must be .meta file'
            .format(tf_filename))
    saver = tf.train.import_meta_graph(tf_filename)
    sess = tf.Session()
    try:
        tf_model_name = tf_filename.replace('.meta', '')
        saver.restore(sess, tf_model_name)
    except Exception:
        print('WARN: Cannot find checkpoint data {}, trying to proceed'
              .format('{}.data-?-of-?'.format(tf_filename)))
    return sess

def import_graph(tf_filename):
    sess = load_tf_session(tf_filename)
    cougr_graph = construct_cougr_graph(sess.graph)
    return cougr_graph

def construct_cougr_graph(tf_graph):
    graph = Graph()
    tensors = {}
    op_inputs = {}
    for tf_op in tf_graph._nodes_by_name.values():
        if tf_op.type in TF_OP_TO_COUGR.keys():
            # Map to CouGr op type
            cougr_type = TF_OP_TO_COUGR[tf_op.type]
        else:
            print('WARN: Unknown op type: {} (op: {})'
                  .format(tf_op.type, tf_op.name))
            cougr_type = UnknownOp

        # Create the CouGr internal op
        op = cougr_type(tf_op.name)

        # Create the output tensors for this op
        for i in range(len(tf_op.outputs)):
            tf_tensor = tf_op.outputs[i]

            tf_dtype = tf_tensor.dtype.base_dtype
            if tf_dtype in TF_DTYPE_TO_COUGR.keys():
                cougr_dtype = TF_DTYPE_TO_COUGR[tf_dtype]
            else:
                print('WARN: Unknown dtype {} for tensor {}'
                      .format(tf_tensor.dtype, tf_tensor))
                cougr_dtype = None

            out_tens = Tensor(tf_tensor.name,
                tf_shape_to_cougr(tf_tensor.shape), cougr_dtype)
            tensors[out_tens.name] = out_tens
            op.addOutput(out_tens)

        # Track the input tensor names to connect them in next phase
        op_inputs[op.name] = []
        for i in range(len(tf_op.inputs)):
            op_inputs[op.name].append(tf_op.inputs[i].name)

        graph.addOp(op)

    # Hook up all the op inputs to the ops that generate them
    for op_name in op_inputs.keys():
        op = graph.opsByName[op_name]
        for in_tensor in op_inputs[op_name]:
            assert in_tensor in tensors.keys(), \
                   'Unknown input tensor {}'.format(in_tensor)
            graph.addInputToOp(op, tensors[in_tensor])

    # Traverse the graph to find subgraph ops, such as loops
    # NOTES:
    #  1) TF while loops are controlled by a LoopConditionOp, which gates
    #     all the SwitchOps that allow a loop iteration to proceed. The
    #     inputs to a LoopConditionOp can be part of the condition function
    #     passed to tf.while_loop. However, the condition function cannot
    #     create side-effects (which is an important observation for
    #     identifying the condition subgraph).
    #  2) The condition subgraph is defined as all inputs to the while loop
    #     that are not updated during the loop body and outputs of MergeOps
    #     that are used to evaluate the loop condition function.
    #  3) Loops create a loop-iteration versioning context for each variable
    #     that is explicitly input into the while condition or body
    #     functions (but NOT variables/tensors that are accessed locally or
    #     globally for evaluating the condition).
    #  4) The body of the loop is all ops that occur between any IdentityOp
    #     and any NextIterationOp from the variable contexts for the loop.
    #  Final) Note that TF while loops can have nested while loops or other
    #     control flow blocks, so we need to design this recursively.
    control_ops = []
    # Find the ops that will require subgraph designations (i.e., control)
    for op_name, op in graph.opsByName.items():
        if op.isControlOp():
            control_ops.append(op)
    for ctrl_op in control_ops:
        # Get all ops for the loop condition value calculation (1 and 2),
        # the variable contexts (3), and the loop body (4). Extract these
        # into a subgraph.
        subgraph_ops = [ctrl_op]
        visited_ops = set(subgraph_ops)
        frontier_ops = []
        for out_tensor in ctrl_op.outputs:
            for consumer in out_tensor.consumers.values():
                assert isinstance(consumer, SwitchOp)
            frontier_ops.extend(out_tensor.consumers.values())

        # A) Traverse backward from SwitchOps to MergeOps and EnterOps,
        #    and NextIterationOps. Stop at the LoopConditionOp, and any
        #    NextIterationOps and EnterOps. Add MergeOps to the frontier
        #    to traverse forward from them.
        bwd_frontier_ops = list(frontier_ops)
        while len(bwd_frontier_ops) > 0:
            next_op = bwd_frontier_ops.pop(0)
            if next_op in visited_ops:
                continue
            assert not next_op.isControlOp(), \
                'CouGr Framework(TF): Should be no up-stream control blocks!'
            visited_ops.add(next_op)
            if isinstance(next_op, EnterOp) or \
               isinstance(next_op, NextIterationOp):
                # Do not traverse past EnterOps, NextIterationOps
                continue
            if isinstance(next_op, MergeOp):
                frontier_ops.append(next_op)
            for in_tensor in next_op.inputs:
                bwd_frontier_ops.append(in_tensor.producer)

        # B) Traverse forward to get the SwitchOps, ExitOps, IdentityOps,
        #    body, NextIterationOps.
        fwd_frontier_ops = []
        for switch_op in frontier_ops:
            for out_tensor in switch_op.outputs:
                fwd_frontier_ops.extend(out_tensor.consumers.values())
        while len(fwd_frontier_ops) > 0:
            next_op = fwd_frontier_ops.pop(0)
            if next_op in visited_ops:
                continue
            if next_op.isControlOp():
                raise NotImplementedError(
                    'CouGr Framework(TF): Need nested control blocks')
            visited_ops.add(next_op)
            if isinstance(next_op, ExitOp):
                # Do not traverse past ExitOps
                continue
            for out_tensor in next_op.outputs:
                fwd_frontier_ops.extend(out_tensor.consumers.values())

        # [_] TODO (Joel): May need to go backward again to other EnterOps or to
        # identify the loop condition that gets executed...

        # Finally, create a ControlBlockOp (subgraph) with the main control
        # node as the ctrl_op, and add the ControlBlockOp to the CouGr graph
        # (which will move the graph ops into the subgraph)
        ctrl_block_op = ControlBlockOp('{}_block'.format(ctrl_op.name),
                                       ctrl_op, visited_ops)
        graph.addOp(ctrl_block_op)

    return graph
