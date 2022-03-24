from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, List, Optional, Union

from qulacs import ParametricQuantumCircuit, QuantumState


class _Axis(Enum):
    """Specifying axis. Used in inner private method in LearningCircuit."""

    X = auto()
    Y = auto()
    Z = auto()


InputFunc = Callable[[List[float]], float]  # Depends on x
# Depends on theta, x
InputFuncWithParam = Callable[[float, List[float]], float]


@dataclass
class _LearningParameter:
    """Manage a parameter of `ParametricQuantumCircuit`.
    This class manages index and value of parameter.
    There is two member variables representing index: `pos` and `theta_pos`.
    `pos` is an index of `ParametricQuantumCircuit`.
    And `theta_pos` is an index of a whole set of learning parameters.
    This is used by method of `LearningCircuit` which has "parametric" in its name.

    Example of a relationship between `pos` and `theta_pos` is following:
    `[(pos, theta_pos)] = [(0, 0), (1, -), (2, 1), (3, 2), (4, -)]`
    Here `-` means absence.

    Args:
        pos: Index of a parameter at LearningCircuit._circuit.
        theta_pos: Index at array of learning parameter(theta).
        value: Current `pos`-th parameter of LearningCircuit._circuit.
        is_input: Whethter this parameter is used with a input parameter.
    """

    pos: int
    theta_pos: int
    value: float
    is_input: bool = field(default=False)


@dataclass
class _InputParameter:
    """Manage transformation of an input.
    `func` transforms the given input and the outcome is stored at `pos`-th parameter in `LearningCircuit._circuit`.
    If the `func` needs a learning parameter, supply `companion_theta_pos` with the learning parameter's `theta_pos`.
    """

    pos: int
    func: Union[InputFunc, InputFuncWithParam]
    companion_theta_pos: Optional[int]


class LearningCircuit:
    """Construct and run quantum circuit for QNN.

    ## About parameters

    This class manages parameters of underlying `ParametricQuantumCircuit`.
    A parameter has either type of features: learning and input.

    Learning parameter represents a parameter to be optimized.
    This is updated by `LearningCircuit.update_parameter()`.

    Input parameter represents a placeholder of circuit input.
    This is updated in a execution of `LearningCircuit.run()` while applying `func` of the parameter.

    And there is a parameter being both learning and input one.
    This parameter transforms its input by applying the parameter's `func` with its learning parameter.

    ## Execution flow

    1. Set up gates by `LearningCircuit.add_*_gate()`.
    2. For each execution, at first, feed input parameter with the value computed from input data `x`.
    3. Apply |0> state to the circuit.
    4. Compute optimized learning parameters in a certain way.
    5. Update the learning parameters in the circuit with the optimized ones by `LearningCircuit.update_parameters()`.

    Args:
        n_qubit: The number of qubits in the circuit.

    Examples:
        >>> from skqulacs.circuit import LearningCircuit
        >>> from skqulacs.qnn.regressor import QNNRegressor
        >>> n_qubit = 2
        >>> circuit = LearningCircuit(n_qubit)
        >>> circuit.add_parametric_RX_gate(0, 0.5)
        >>> circuit.add_input_RZ_gate(1, np.arcsin)
        >>> model = QNNRegressor(circuit)
        >>> _, theta = model.fit(x_train, y_train, maxiter=1000)
        >>> x_list = np.arange(x_min, x_max, 0.02)
        >>> y_pred = qnn.predict(theta, x_list)
    """

    def __init__(
        self,
        n_qubit: int,
    ) -> None:
        self.n_qubit = n_qubit
        self._circuit = ParametricQuantumCircuit(n_qubit)
        self._learning_parameter_list: List[_LearningParameter] = []
        self._input_parameter_list: List[_InputParameter] = []

    def update_parameters(self, theta: List[float]) -> None:
        """Update learning parameter of the circuit.

        Args:
            theta: New learning parameter.
        """
        for parameter in self._learning_parameter_list:
            parameter_value = theta[parameter.theta_pos]
            parameter.value = parameter_value
            self._circuit.set_parameter(parameter.pos, parameter_value)

    def get_parameters(self) -> List[float]:
        """Get a list of learning parameters."""
        theta_list = [p.value for p in self._learning_parameter_list]
        return theta_list

    def _set_input(self, x: List[float]) -> None:
        for parameter in self._input_parameter_list:
            # Input parameter is updated here, not update_parameters(),
            # because input parameter is determined with the input data `x`.
            if parameter.companion_theta_pos is None:
                # If `companion_theta_pos` is `None`, `func` does not need a learning parameter.
                angle = parameter.func(x)
            else:
                theta = self._learning_parameter_list[parameter.companion_theta_pos]
                angle = parameter.func(theta.value, x)
                theta.value = angle
            self._circuit.set_parameter(parameter.pos, angle)

    def run(self, x: List[float] = list()) -> QuantumState:
        """Determine parameters for input gate based on `x` and apply the circuit to |0> state.

        Arguments:
            x: Input data whose shape is (n_features,).

        Returns:
            Quantum state applied the circuit.
        """
        state = QuantumState(self.n_qubit)
        state.set_zero_state()
        self._set_input(x)
        self._circuit.update_quantum_state(state)
        return state

    def run_x_no_change(self) -> QuantumState:
        """
        Run the circuit while x is not changed from the previous run.
        (can change parameters)
        """
        state = QuantumState(self.n_qubit)
        state.set_zero_state()
        self._circuit.update_quantum_state(state)
        return state

    def backprop(self, x: List[float], obs) -> List[float]:
        """
        xは入力の状態で、yは出力値の微分値
        帰ってくるのは、それぞれのパラメータに関する微分値
        例えば、出力が[0,2]
        だったらパラメータの1項目は期待する出力に関係しない、2項目をa上げると回路の出力は2a上がる?

        ->
        c++のParametricQuantumCircuitクラスを呼び出す
        backprop(GeneralQuantumOperator* obs)

        ->うまくやってbackpropする。
        現実だと不可能な演算も含むが、気にしない
        """
        self._set_input(x)
        ret = self._circuit.backprop(obs)
        ans = [0] * len(self._learning_parameter_list)
        for parameter in self._learning_parameter_list:
            if not parameter.is_input:
                ans[parameter.theta_pos] = ret[parameter.pos]

        return ans

    def add_gate(self, gate) -> None:
        """Add arbitrary gate.

        Args:
            gate: Gate to add.
        """
        self._circuit.add_gate(gate)

    def add_X_gate(self, index: int) -> None:
        """
        Args:
            index: Index of qubit to add X gate.
        """
        self._circuit.add_X_gate(index)

    def add_Y_gate(self, index: int) -> None:
        """
        Args:
            index: Index of qubit to add Y gate.
        """
        self._circuit.add_Y_gate(index)

    def add_Z_gate(self, index: int) -> None:
        """
        Args:
            index: Index of qubit to add Z gate.
        """
        self._circuit.add_Z_gate(index)

    def add_RX_gate(self, index: int, angle: float) -> None:
        """
        Args:
            index: Index of qubit to add RX gate.
            angle: Rotation angle.
        """
        self._add_R_gate_inner(index, angle, _Axis.X)

    def add_RY_gate(self, index: int, parameter: float) -> None:
        """
        Args:
            index: Index of qubit to add RY gate.
            angle: Rotation angle.
        """
        self._add_R_gate_inner(index, parameter, _Axis.Y)

    def add_RZ_gate(self, index: int, parameter: float) -> None:
        """
        Args:
            index: Index of qubit to add RZ gate.
            angle: Rotation angle.
        """
        self._add_R_gate_inner(index, parameter, _Axis.Z)

    def add_CNOT_gate(self, indexA: int, indexB: int) -> None:
        """
        Args:
            indexA: Index of qubit to CNOT gate.
            indexB: Index of qubit to CNOT gate.
        """
        self._circuit.add_CNOT_gate(indexA, indexB)

    def add_H_gate(self, index: int) -> None:
        """
        Args:
            index: Index of qubit to H gate.
        """
        self._circuit.add_H_gate(index)

    def add_input_RX_gate(
        self,
        index: int,
        input_func: InputFunc = lambda x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RX gate.
            input_func: Function transforming index value.
        """
        self._add_input_R_gate_inner(index, _Axis.X, input_func)

    def add_input_RY_gate(
        self,
        index: int,
        input_func: InputFunc = lambda x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RY gate.
            input_func: Function transforming index value.
        """
        self._add_input_R_gate_inner(index, _Axis.Y, input_func)

    def add_input_RZ_gate(
        self,
        index: int,
        input_func: InputFunc = lambda x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RZ gate.
            input_func: Function transforming index value.
        """
        self._add_input_R_gate_inner(index, _Axis.Z, input_func)

    def add_parametric_RX_gate(self, index: int, parameter: float) -> None:
        """
        Args:
            index: Index of qubit to add RX gate.
            parameter: Initial parameter of this gate.
        """
        self._add_parametric_R_gate_inner(index, parameter, _Axis.X)

    def add_parametric_RY_gate(self, index: int, parameter: float) -> None:
        """
        Args:
            index: Index of qubit to add RY gate.
            parameter: Initial parameter of this gate.
        """
        self._add_parametric_R_gate_inner(index, parameter, _Axis.Y)

    def add_parametric_RZ_gate(self, index: int, parameter: float) -> None:
        """
        Args:
            index: Index of qubit to add RZ gate.
            parameter: Initial parameter of this gate.
        """
        self._add_parametric_R_gate_inner(index, parameter, _Axis.Z)

    def add_parametric_input_RX_gate(
        self,
        index: int,
        parameter: float,
        input_func: InputFuncWithParam = lambda theta, x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RX gate.
            parameter: Initial parameter of this gate.
            input_func: Function transforming this gate's parameter and index value.
        """
        self._add_parametric_input_R_gate_inner(index, parameter, _Axis.X, input_func)

    def add_parametric_input_RY_gate(
        self,
        index: int,
        parameter: float,
        input_func: InputFuncWithParam = lambda theta, x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RY gate.
            parameter: Initial parameter of this gate.
            input_func: Function transforming this gate's parameter and index value.
        """
        self._add_parametric_input_R_gate_inner(index, parameter, _Axis.Y, input_func)

    def add_parametric_input_RZ_gate(
        self,
        index: int,
        parameter: float,
        input_func: InputFuncWithParam = lambda theta, x: x[0],
    ) -> None:
        """
        Args:
            index: Index of qubit to add RZ gate.
            parameter: Initial parameter of this gate.
            input_func: Function transforming this gate's parameter and index value.
        """
        self._add_parametric_input_R_gate_inner(index, parameter, _Axis.Z, input_func)

    def _add_R_gate_inner(
        self,
        index: int,
        angle: Optional[float],
        target: _Axis,
    ) -> None:
        if target == _Axis.X:
            self._circuit.add_RX_gate(index, angle)
        elif target == _Axis.Y:
            self._circuit.add_RY_gate(index, angle)
        elif target == _Axis.Z:
            self._circuit.add_RZ_gate(index, angle)
        else:
            raise NotImplementedError

    def _add_parametric_R_gate_inner(
        self,
        index: int,
        parameter: float,
        target: _Axis,
    ) -> None:
        learning_parameter = _LearningParameter(
            self._circuit.get_parameter_count(),
            len(self._learning_parameter_list),
            parameter,
        )
        self._learning_parameter_list.append(learning_parameter)

        if target == _Axis.X:
            self._circuit.add_parametric_RX_gate(index, parameter)
        elif target == _Axis.Y:
            self._circuit.add_parametric_RY_gate(index, parameter)
        elif target == _Axis.Z:
            self._circuit.add_parametric_RZ_gate(index, parameter)
        else:
            raise NotImplementedError

    def _add_input_R_gate_inner(
        self,
        index: int,
        target: _Axis,
        input_func: InputFunc,
    ) -> None:
        self._input_parameter_list.append(
            _InputParameter(self._circuit.get_parameter_count(), input_func, None)
        )

        # Input gate is implemented with parametric gate because this gate should be
        # updated with input data in every iteration.
        if target == _Axis.X:
            self._circuit.add_parametric_RX_gate(index, 0.0)
        elif target == _Axis.Y:
            self._circuit.add_parametric_RY_gate(index, 0.0)
        elif target == _Axis.Z:
            self._circuit.add_parametric_RZ_gate(index, 0.0)
        else:
            raise NotImplementedError

    def _add_parametric_input_R_gate_inner(
        self,
        index: int,
        parameter: float,
        target: _Axis,
        input_func: InputFuncWithParam,
    ) -> None:
        pos = self._circuit.get_parameter_count()

        learning_parameter = _LearningParameter(
            pos, len(self._learning_parameter_list), parameter, True
        )
        self._learning_parameter_list.append(learning_parameter)

        self._input_parameter_list.append(
            _InputParameter(pos, input_func, learning_parameter.theta_pos)
        )

        if target == _Axis.X:
            self._circuit.add_parametric_RX_gate(index, parameter)
        elif target == _Axis.Y:
            self._circuit.add_parametric_RY_gate(index, parameter)
        elif target == _Axis.Z:
            self._circuit.add_parametric_RZ_gate(index, parameter)
        else:
            raise NotImplementedError
