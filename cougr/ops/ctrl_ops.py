import sympy

from .base_op import Op
from ..graph import Graph


# TODO (Joel): Refactor this into a Subgraph object that inherits just from Op
# and migrates most of the graph functionality here
class ControlBlockOp(Op, Graph):
    ''' A ControlBlockOp designates a subgraph that manages some form of
        dynamic control flow for a compute graph (e.g., if-conditionals or
        while loops). Such ops are actually a collection of ops that perform
        the dynamic control operations. NOTE: ControlBlockOps can contain
        other ControlBlockOps (nesting).
    '''
    def __init__(self, name, root_op, ops_list):
        super(ControlBlockOp, self).__init__(name)
        assert isinstance(root_op, Op)
        self._root_op = root_op
        self._ops_by_name = {}
        # Maintain a list of the ops that are sources to the graph. In
        # particular, if an op has no inputs or any of its inputs are
        # produced by ops outside the graph, then it is a source op.
        self._sources = {}
        # Maintain a list of the ops that are sinks from the graph. In
        # particular, if none of the op's outputs are consumed by any op
        # (i.e., terminal node) or they are consumed by other ops outside
        # the graph, then it is a sink op.
        self._sinks = {}

        for op in ops_list:
            self.addOp(op)
        self.findAllSourcesSinks()

    @property
    def inputs(self):
        # Collect the inputs to all sources and return
        to_return = set()
        for source_op in self._sources.values():
            for in_tensor in source_op.inputs:
                to_return.add(in_tensor)
        return list(to_return)

    @property
    def outputs(self):
        # Collect the outputs of all sinks and return
        to_return = set()
        for sink_op in self._sinks.values():
            for out_tensor in sink_op.outputs:
                for consumer in out_tensor.consumers.values():
                    to_return.add(out_tensor)
        return list(to_return)

    def findAllSourcesSinks(self):
        for op in self._ops_by_name.values():
            # Check if op is a source to the subgraph
            if op.name not in self._sources.keys():
                is_source = False
                for in_tensor in op.inputs:
                    if in_tensor.producer.name not in self._ops_by_name.keys():
                        is_source = True
                        break
                if is_source:
                    self._sources[op.name] = op
            # Check if the op is a sink of the subgraph
            if op.name not in self._sinks.keys():
                is_sink = False
                for out_tensor in op.outputs:
                    for consumer in out_tensor.consumers.keys():
                        if consumer not in self._ops_by_name.keys():
                            is_sink = True
                            break
                if is_sink:
                    self._sinks[op.name] = op

    def propagateShapes(self):
        # Propagating shapes is a flattened operation, so control blocks
        # do not need to do any work for them
        pass

    def calcAlgFlops(self):
        if not isinstance(self._root_op, LoopConditionOp):
            raise NotImplementedError(
                'ControlBlockOp {} has unknown _root_op type {}'
                .format(self.name, type(self._root_op)))

        loop_iter_name = '{}::iters'.format(self.name)
        loop_iters = sympy.Symbol(loop_iter_name)
        return loop_iters * Graph.calcAlgFlops(self)


class EnterOp(Op):
    ''' EnterOp designates the start of a control flow operation that acts
        on the input tensor to the op. The output tensor is just the input
        tensor, but the output tensor may need to be annotated with
        information about the control flow path it is on. For example, for
        variables used inside dynamic loops, the tensor may need to track
        the dynamic instance ID.
    '''
    def __init__(self, name):
        super(EnterOp, self).__init__(name)

    def propagateShapes(self):
        # EnterOps should forward their inputs to their outputs
        assert len(self._inputs) == 1
        assert len(self._outputs) == 1
        if not self._inputs[0].shape.isUnknown():
            if self._inputs[0].shape != self._outputs[0].shape:
                raise NotImplementedError('EnterOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[0].shape.mergeShape(self._inputs[0].shape)
        else:
            fail_str = 'EnterOp {} propagateShapes unknown input shape' \
                       .format(self._name)
            raise NotImplementedError(fail_str)

    def calcAlgFlops(self):
        # EnterOps perform no calculations
        return 0


class ExitOp(Op):
    ''' ExitOp designates the end of a control flow operation that acts
        on the input tensor to the op. ExitOps make the input tensor
        available to downstream ops (i.e., outside of the context formed
        by the EnterOp-ExitOp pair).
    '''
    def __init__(self, name):
        super(ExitOp, self).__init__(name)

    def propagateShapes(self):
        # ExitOps have no outputs to propagate to
        assert len(self._inputs) == 1, 'Op: {}'.format(self._name)
        assert len(self._outputs) == 1, 'Op: {}'.format(self._name)
        if not self._inputs[0].shape.isUnknown():
            if self._inputs[0].shape != self._outputs[0].shape:
                raise NotImplementedError('ExitOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[0].shape.mergeShape(self._inputs[0].shape)
        else:
            fail_str = 'ExitOp {} propagateShapes unknown input shape' \
                       .format(self._name)
            raise NotImplementedError(fail_str)

    def calcAlgFlops(self):
        # ExitOps perform no calculations
        return 0


class LoopConditionOp(Op):
    ''' LoopConditionOp takes a boolean input and passes it out to SwitchOps
        as part of dynamic loops. It is a unique identifier op for loops, so
        it is considered to be a control op.
    '''
    def __init__(self, name):
        super(LoopConditionOp, self).__init__(name)

    def isControlOp(self):
        return True

    def propagateShapes(self):
        # LoopConditionOps forward their input to their output
        # [_] TODO (Joel): If shapes are unspecified, bind them
        assert len(self._inputs) == 1
        assert self._inputs[0].shape.numElements() == 1
        for out_tensor in self._outputs:
            assert out_tensor.shape.numElements() == 1

    def calcAlgFlops(self):
        # LoopConditionOps perform no calculations
        return 0


class MergeOp(Op):
    ''' MergeOp forwards the value of the first available tensor to the first
        output and sets the second output equal to the index of the first
        available input.
    '''
    def __init__(self, name):
        super(MergeOp, self).__init__(name)

    def canVisit(self, visited_ops):
        ''' Whether this op can be visited given the previous ops that
            have been visited according to the input set visited_ops.
            By default, most ops require that all producer tensors are
            ready before they can be performed. Other ops must override
            this function to get different functionality.
            Args:
                visited_ops: A set of ops that have been previously
                             visited in the graph
        '''
        # Check if any inputs are ready
        ready_in_tensors = set()
        for in_tensor in self._inputs:
            if in_tensor.producer in visited_ops:
                ready_in_tensors.add(in_tensor)
        # If at least one input tensor is ready, then can visit
        return len(ready_in_tensors) > 0

    def propagateShapes(self):
        # MergeOps forward their input to their output for the
        # next iteration of a loop
        assert len(self._inputs) >= 1
        assert len(self._outputs) == 2
        # NOTE: Any of the input shapes can be unknown, so find one that is
        # known (if one does not exist, cannot propagate)
        in_shape = None
        for in_tensor in self._inputs:
            if not in_tensor.shape.isUnknown():
                if in_shape is not None:
                    # Verify that all input tensor can be merged
                    assert in_tensor.shape.canBroadcastTogether(in_shape)
                else:
                    in_shape = in_tensor.shape
        if not in_shape.isUnknown():
            if in_shape != self._outputs[0].shape:
                raise NotImplementedError('MergeOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[0].shape.mergeShape(in_shape)
        else:
            fail_str = 'MergeOp {} propagateShapes unknown input shape' \
                       .format(self._name)
            raise NotImplementedError(fail_str)

    def calcAlgFlops(self):
        # MergeOps perform no calculations
        return 0


class NextIterationOp(Op):
    ''' NextIterationOp forwards its input to its output for loops.
    '''
    def __init__(self, name):
        super(NextIterationOp, self).__init__(name)

    def propagateShapes(self):
        # NextIterationOps forward their input to their output for the
        # next iteration of a loop
        assert len(self._inputs) == 1
        assert len(self._outputs) == 1
        if not self._inputs[0].shape.isUnknown():
            if self._inputs[0].shape != self._outputs[0].shape:
                raise NotImplementedError('NextIterationOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[0].shape.mergeShape(self._inputs[0].shape)
        else:
            fail_str = 'NextIterationOp {} propagateShapes unknown input '\
                       ' shape'.format(self._name)
            raise NotImplementedError(fail_str)

    def calcAlgFlops(self):
        # NextIterationOps perform no calculations
        return 0


class SwitchOp(Op):
    ''' The first input to the SwitchOp is the tensor that should be
        forwarded to one of the outputs. The second input gates whether the
        first input gets forwarded to the first or second output. If the
        second input is true, input goes to the first output, or if the
        second input is false, input goes to the second output.
    '''
    def __init__(self, name):
        super(SwitchOp, self).__init__(name)

    def propagateShapes(self):
        # SwitchOps have two inputs and two outputs, and they conditionally
        # propagate the first input either to the first or second output
        # depending on whether the second input is true or false, resp.
        assert len(self._inputs) == 2
        assert self._inputs[1].shape.isScalar()
        assert len(self._outputs) == 2
        if self._inputs[0].shape.isUnknown():
            fail_str = 'SwitchOp {} propagateShapes unknown input shape' \
                       .format(self._name)
            raise NotImplementedError(fail_str)
        else:
            if self._inputs[0].shape != self._outputs[0].shape:
                raise NotImplementedError('SwitchOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[0].shape.mergeShape(self._inputs[0].shape)
            if self._inputs[0].shape != self._outputs[1].shape:
                raise NotImplementedError('SwitchOp propagateShapes {}'
                                          .format(self._name))
            self._outputs[1].shape.mergeShape(self._inputs[0].shape)

    def calcAlgFlops(self):
        # SwitchOps perform no calculations
        return 0
