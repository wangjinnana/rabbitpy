"""
Base classes for various parts of rabbitpy

"""
import logging
try:
    import queue
except ImportError:
    import Queue as queue
from pamqp import specification
import time

from rabbitpy import exceptions

LOGGER = logging.getLogger(__name__)


class AMQPClass(object):
    """Base Class object for wrapping the specification.Frame classes

    """
    def __init__(self, channel, name):
        """Create a new ClassObject.

        :param rabbitpy.Channel channel: The channel to execute commands on
        :param str name: Set the name

        """
        self.name = name
        self.channel = channel

    def _rpc(self, frame_value):
        """Execute the RPC command for the frame.

        :param pamqp.specification.Frame frame_value: The frame to send
        :rtype: pamqp.specification.Frame | pamqp.message.Message

        """
        if self.channel.closed:
            raise exceptions.ChannelClosedException()
        return self.channel.rpc(frame_value)

    def _write_frame(self, frame_value):
        """Write a frame to the channel's connection

        :param pamqp.specification.Frame frame_value: The frame to send

        """
        self.channel._write_frame(frame_value)


class StatefulObject(object):
    """Base object for rabbitpy classes that need to maintain state such as
    connection and channel.

    """
    CLOSED = 0x00
    CLOSING = 0x01
    OPEN = 0x02
    OPENING = 0x03

    STATES = {0x00: 'Closed',
              0x01: 'Closing',
              0x02: 'Open',
              0x03: 'Opening'}

    def __init__(self):
        """Create a new instance of the object defaulting to a closed state."""
        self._state = self.CLOSED

    def _set_state(self, value):
        """Set the state to the specified value, validating it is a supported
        state value.

        :param int value: The new state value
        :raises: ValueError
        """
        if value not in list(self.STATES.keys()):
            raise ValueError('Invalid state value: %r' % value)
        LOGGER.debug('%s setting state to %r', self.__class__.__name__,
                     self.STATES[value])
        self._state = value

    @property
    def closed(self):
        """Returns True if in the CLOSED runtime state

        :rtype: bool

        """
        return self._state == self.CLOSED

    @property
    def closing(self):
        """Returns True if in the CLOSING runtime state

        :rtype: bool

        """
        return self._state == self.CLOSING

    @property
    def open(self):
        """Returns True if in the OPEN runtime state

        :rtype: bool

        """
        return self._state == self.OPEN

    @property
    def opening(self):
        """Returns True if in the OPENING runtime state

        :rtype: bool

        """
        return self._state == self.OPENING

    @property
    def state(self):
        """Return the runtime state value

        :rtype: int

        """
        return self._state

    @property
    def state_description(self):
        """Returns the text based description of the runtime state

        :rtype: str

        """
        return self.STATES[self._state]


class AMQPChannel(StatefulObject):

    CLOSE_REQUEST_FRAME = specification.Channel.Close
    DEFAULT_CLOSE_CODE = 200
    DEFAULT_CLOSE_REASON = 'Normal Shutdown'

    def __init__(self, exception_queue):
        super(AMQPChannel, self).__init__()
        self._channel_id = None
        self._exceptions = exception_queue
        self._state = self.CLOSED
        self._read_queue = None
        self._write_queue = None

    def __int__(self):
        return self._channel_id

    def _build_close_frame(self):
        return self.CLOSE_REQUEST_FRAME(self.DEFAULT_CLOSE_CODE,
                                        self.DEFAULT_CLOSE_REASON)

    def _check_for_exceptions(self):
        if not self._exceptions.empty():
            exception = self._exceptions.get()
            raise exception

    def _close(self):
        if self.closing or self.closed:
            return
        self._set_state(self.CLOSING)
        frame_value = self._build_close_frame()
        self._write_frame(frame_value)
        LOGGER.debug('Waiting for a valid response for %s', frame_value.name)
        self._wait_on_frame(frame_value.valid_responses)
        self._set_state(self.CLOSED)

    def _read_from_queue(self):
        self._check_for_exceptions()
        if not self.closed:
            try:
                return self._read_queue.get(True, 3)
            except queue.Empty:
                pass

    def _validate_frame_type(self, frame_value, frame_type):
        """Validate the frame value against the frame type. The frame type can
        be an individual frame type or a list of frame types.

        :param pamqp.specification.Frame frame_value: The frame to check
        :param frame_type: The frame(s) to check against
        :type frame_type: pamqp.specification.Frame|list
        :rtype: bool

        """
        if frame_value is None:
            LOGGER.debug('No frame value passed in')
            return False
        if isinstance(frame_type, str):
            if frame_value.name == frame_type:
                return True
        elif isinstance(frame_type, list):
            for frame_t in frame_type:
                result = self._validate_frame_type(frame_value, frame_t)
                if result:
                    return True
            return False
        elif isinstance(frame_value, specification.Frame):
            return frame_value.name == frame_type.name
        return False

    def _wait_on_frame(self, frame_type=None):
        """Read from the queue, blocking until a result is returned. An
        individual frame type or a list of frame types can be passed in to wait
        for specific frame types. If there is no match on the frame retrieved
        from the queue, put the frame back in the queue and recursively
        call the method.

        :param frame_type: The name or list of names of the frame type(s)
        :type frame_type:  str|list|specification.Frame
        :rtype: Frame

        """
        if not frame_type:
            return self._read_from_queue()

        while not self.closed:
            value = self._read_from_queue()
            if self._validate_frame_type(value, frame_type):
                return value
            self._read_queue.put(value)

    def _write_frame(self, frame):
        self._write_queue.put((self._channel_id, frame))
