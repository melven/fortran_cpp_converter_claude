!> program settings: plain C struct data (hand-wrapped: [types] cpp = the C struct, [modules] header = its C header)
module settings_mod
  use, intrinsic :: iso_c_binding, only: C_PTR, C_NULL_PTR, C_INT
  implicit none
  private
  public :: ProgramSettings_type

  type, bind(C) :: ProgramSettings_type
    type(C_PTR) :: name = C_NULL_PTR     !< const char*
    integer(C_INT) :: level = 0
  end type ProgramSettings_type
end module settings_mod
