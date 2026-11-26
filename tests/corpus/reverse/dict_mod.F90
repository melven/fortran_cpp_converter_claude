!> entry type of a Fortran dictionary (base of factories, Fortran only)
module dict_mod
  implicit none
  private
  public :: DictEntry_type

  type, abstract :: DictEntry_type
    character(len=:), allocatable :: key
  end type DictEntry_type
end module dict_mod
