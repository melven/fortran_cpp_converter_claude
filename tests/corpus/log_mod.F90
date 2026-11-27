! log_mod: long log messages (a local message string in C++)
module log_mod
  implicit none
contains
  !> logs a message
  subroutine log_error(name, msg)
    character(len=*), intent(in) :: name    !< logger name
    character(len=*), intent(in) :: msg     !< message
    print *, name, ': ', msg
  end subroutine log_error

  !> integer as text
  function str(i) result(s)
    integer, intent(in) :: i    !< value
    character(len=12) :: s
    write(s, '(i0)') i
  end function str

  !> reports the iteration
  subroutine report(iter, nfail, msg)
    integer, intent(in) :: iter     !< iteration
    integer, intent(in) :: nfail    !< failures
    integer, intent(in) :: msg      !< a variable named msg
    call log_error("solver", "Iteration " // trim(str(iter)) // ", failures " // trim(str(nfail)) // ", code " // trim(str(msg)))
    call log_error("solver", "short")
    if (nfail > 0) then
      call log_error("solver", "Failures in iteration " // trim(str(iter)) // ": " // trim(str(nfail)) // " of " // trim(str(msg)))
    end if
  end subroutine report
end module log_mod
